"""
Stripe Integration Routes
Handles subscription checkout, webhooks, and subscription management
"""
import os
import stripe
from fastapi import APIRouter, Request, HTTPException, Header
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from typing import Optional
import logging
from datetime import datetime
from supabase import create_client, Client

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/billing", tags=["billing"])

# Stripe configuration
stripe.api_key = os.getenv("STRIPE_SECRET_KEY", "")
STRIPE_WEBHOOK_SECRET = os.getenv("STRIPE_WEBHOOK_SECRET", "")

# Supabase configuration
SUPABASE_URL = os.getenv("SUPABASE_URL", "")
SUPABASE_KEY = os.getenv("SUPABASE_SERVICE_KEY", "")

# Subscription plans configuration
PLANS = {
    "starter": {
        "id": "starter",
        "name": "Starter",
        "price": 0,
        "price_id": None,  # Free plan
        "features": ["1 project", "1,000 deploys/month", "Community support"],
        "deployments_limit": 1000,
        "projects_limit": 1,
        "priority": "normal"
    },
    "pro": {
        "id": "pro",
        "name": "Pro",
        "price": 29,
        "price_id": "price_pro_monthly",
        "features": ["Unlimited projects", "50,000 deploys/month", "Priority support", "Custom domains"],
        "deployments_limit": 50000,
        "projects_limit": -1,
        "priority": "high"
    },
    "team": {
        "id": "team",
        "name": "Team",
        "price": 99,
        "price_id": "price_team_monthly",
        "features": ["Unlimited projects", "Unlimited deploys", "Dedicated support", "SSO", "Audit logs"],
        "deployments_limit": -1,
        "projects_limit": -1,
        "priority": "priority"
    }
}


def get_supabase_client() -> Client:
    """Get Supabase client for database operations"""
    return create_client(SUPABASE_URL, SUPABASE_KEY)


def get_plan_from_price_id(price_id: str) -> Optional[str]:
    """Get plan ID from Stripe price ID"""
    for plan_id, plan in PLANS.items():
        if plan["price_id"] == price_id:
            return plan_id
    return None


class CheckoutRequest(BaseModel):
    plan_id: str
    user_id: str
    email: str
    success_url: str
    cancel_url: str


@router.get("/plans")
async def get_plans():
    """Get available subscription plans"""
    return {
        "plans": [
            {
                "id": plan["id"],
                "name": plan["name"],
                "price": plan["price"],
                "features": plan["features"]
            }
            for plan in PLANS.values()
        ]
    }


@router.post("/checkout")
async def create_checkout_session(request: CheckoutRequest):
    """Create Stripe checkout session for subscription"""
    try:
        plan = PLANS.get(request.plan_id)
        if not plan:
            raise HTTPException(status_code=400, detail="Invalid plan ID")
        
        if plan["price_id"] is None:
            raise HTTPException(status_code=400, detail="Cannot checkout for free plan")
        
        # Create Stripe checkout session
        checkout_session = stripe.checkout.Session.create(
            customer_email=request.email,
            metadata={
                "user_id": request.user_id,
                "plan_id": request.plan_id
            },
            line_items=[
                {
                    "price_data": {
                        "currency": "usd",
                        "product_data": {
                            "name": f"Pipeline AI - {plan['name']} Plan",
                            "description": f"Monthly subscription to {plan['name']} plan"
                        },
                        "unit_amount": plan["price"] * 100,  # Stripe uses cents
                        "recurring": {"interval": "month"}
                    },
                    "quantity": 1
                }
            ],
            mode="subscription",
            success_url=request.success_url,
            cancel_url=request.cancel_url
        )
        
        return {
            "checkout_url": checkout_session.url,
            "session_id": checkout_session.id
        }
    
    except stripe.error.StripeError as e:
        logger.error(f"Stripe error: {str(e)}")
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"Error creating checkout session: {str(e)}")
        raise HTTPException(status_code=500, detail="Failed to create checkout session")


@router.post("/webhook")
async def stripe_webhook(request: Request):
    """Handle Stripe webhook events for subscription management with database updates"""
    payload = await request.body()
    sig_header = request.headers.get("stripe-signature")
    
    try:
        event = stripe.Webhook.construct_event(
            payload, sig_header, STRIPE_WEBHOOK_SECRET
        )
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid payload")
    except stripe.error.SignatureVerificationError:
        raise HTTPException(status_code=400, detail="Invalid signature")
    
    # Get Supabase client
    supabase = get_supabase_client()
    
    # Handle the event
    event_type = event["type"]
    
    if event_type == "checkout.session.completed":
        session = event["data"]["object"]
        
        # Get metadata
        user_id = session.get("metadata", {}).get("user_id")
        plan_id = session.get("metadata", {}).get("plan_id")
        customer_id = session.get("customer")
        subscription_id = session.get("subscription")
        
        logger.info(f"Checkout completed for user {user_id}, plan {plan_id}")
        
        if user_id and plan_id:
            # Update subscription in database
            subscription_data = {
                "user_id": user_id,
                "plan": plan_id,
                "status": "active",
                "stripe_customer_id": customer_id,
                "stripe_subscription_id": subscription_id,
                "updated_at": datetime.utcnow().isoformat()
            }
            
            try:
                # Upsert subscription (insert or update)
                result = supabase.table("subscriptions").upsert(
                    subscription_data,
                    on_conflict="user_id"
                ).execute()
                logger.info(f"Subscription updated for user {user_id}: {result}")
            except Exception as e:
                logger.error(f"Failed to update subscription: {str(e)}")
        
    elif event_type == "customer.subscription.updated":
        subscription = event["data"]["object"]
        subscription_id = subscription.get("id")
        status = subscription.get("status")
        
        logger.info(f"Subscription {subscription_id} updated to status: {status}")
        
        # Update subscription status in database
        try:
            result = supabase.table("subscriptions").update({
                "status": status,
                "current_period_start": datetime.fromtimestamp(subscription.get("current_period_start")).isoformat() if subscription.get("current_period_start") else None,
                "current_period_end": datetime.fromtimestamp(subscription.get("current_period_end")).isoformat() if subscription.get("current_period_end") else None,
                "cancel_at_period_end": subscription.get("cancel_at_period_end", False),
                "updated_at": datetime.utcnow().isoformat()
            }).eq("stripe_subscription_id", subscription_id).execute()
            logger.info(f"Subscription status updated: {result}")
        except Exception as e:
            logger.error(f"Failed to update subscription status: {str(e)}")
    
    elif event_type == "customer.subscription.deleted":
        subscription = event["data"]["object"]
        subscription_id = subscription.get("id")
        
        logger.info(f"Subscription {subscription_id} cancelled")
        
        # Downgrade to free/starter plan
        try:
            result = supabase.table("subscriptions").update({
                "plan": "starter",
                "status": "canceled",
                "stripe_subscription_id": None,
                "updated_at": datetime.utcnow().isoformat()
            }).eq("stripe_subscription_id", subscription_id).execute()
            logger.info(f"Subscription downgraded to starter: {result}")
        except Exception as e:
            logger.error(f"Failed to downgrade subscription: {str(e)}")
    
    elif event_type == "invoice.payment_succeeded":
        invoice = event["data"]["object"]
        logger.info(f"Payment succeeded for invoice {invoice['id']}")
        # Payment successful - subscription is active (handled by subscription.updated)
        
    elif event_type == "invoice.payment_failed":
        invoice = event["data"]["object"]
        subscription_id = invoice.get("subscription")
        logger.warning(f"Payment failed for subscription {subscription_id}")
        
        # Mark subscription as past_due
        try:
            result = supabase.table("subscriptions").update({
                "status": "past_due",
                "updated_at": datetime.utcnow().isoformat()
            }).eq("stripe_subscription_id", subscription_id).execute()
            logger.info(f"Subscription marked as past_due: {result}")
        except Exception as e:
            logger.error(f"Failed to update subscription status: {str(e)}")
    
    else:
        logger.info(f"Unhandled event type: {event_type}")
    
    return {"status": "success"}


@router.get("/subscription/{user_id}")
async def get_subscription(user_id: str):
    """Get user's current subscription status from Supabase"""
    try:
        supabase = get_supabase_client()
        result = supabase.table("subscriptions").select("*").eq("user_id", user_id).single().execute()
        
        if result.data:
            return result.data
        else:
            # Return default free subscription if none exists
            return {
                "user_id": user_id,
                "plan": "starter",
                "status": "active",
                "current_period_end": None,
                "cancel_at_period_end": False
            }
    except Exception as e:
        logger.error(f"Failed to fetch subscription: {str(e)}")
        # Return default on error
        return {
            "user_id": user_id,
            "plan": "starter",
            "status": "active",
            "current_period_end": None,
            "cancel_at_period_end": False
        }


@router.post("/cancel-subscription")
async def cancel_subscription(user_id: str, subscription_id: str):
    """Cancel user's subscription at period end"""
    try:
        subscription = stripe.Subscription.modify(
            subscription_id,
            cancel_at_period_end=True
        )
        
        return {
            "status": "success",
            "cancel_at_period_end": subscription.cancel_at_period_end,
            "current_period_end": subscription.current_period_end
        }
    except stripe.error.StripeError as e:
        logger.error(f"Stripe error cancelling subscription: {str(e)}")
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/portal-session")
async def create_portal_session(customer_id: str, return_url: str):
    """Create Stripe customer portal session for managing subscription"""
    try:
        session = stripe.billing_portal.Session.create(
            customer=customer_id,
            return_url=return_url
        )
        
        return {"portal_url": session.url}
    except stripe.error.StripeError as e:
        logger.error(f"Stripe error creating portal: {str(e)}")
        raise HTTPException(status_code=400, detail=str(e))
