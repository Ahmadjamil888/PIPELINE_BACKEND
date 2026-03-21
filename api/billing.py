# H:\pipeline\backend\api\billing.py

import hmac
import hashlib
import httpx
import os
from fastapi import APIRouter, Request, HTTPException, BackgroundTasks
from pydantic import BaseModel
from dotenv import load_dotenv

load_dotenv()

router = APIRouter(prefix="/billing", tags=["Billing"])

POLAR_API = "https://api.polar.sh/v1"
POLAR_TOKEN = os.getenv("POLAR_ACCESS_TOKEN", "")
POLAR_WEBHOOK_SECRET = os.getenv("POLAR_WEBHOOK_SECRET", "")

PRODUCT_MAP = {
    "starter": os.getenv("POLAR_PRODUCT_ID_STARTER", ""),
    "pro":     os.getenv("POLAR_PRODUCT_ID_PRO", ""),
    "team":    os.getenv("POLAR_PRODUCT_ID_TEAM", ""),
}


# ── Helpers ───────────────────────────────────────────────────────────────────

def polar_headers() -> dict:
    return {
        "Authorization": f"Bearer {POLAR_TOKEN}",
        "Accept": "application/json",
        "Content-Type": "application/json",
    }


def product_to_plan(product_id: str) -> str:
    for plan, pid in PRODUCT_MAP.items():
        if pid == product_id:
            return plan
    return "free"


async def get_polar_customer_id(user_id: str) -> str | None:
    """Look up Polar customer by your internal user_id (external_id)"""
    async with httpx.AsyncClient() as client:
        res = await client.get(
            f"{POLAR_API}/customers",
            headers=polar_headers(),
            params={"external_id": user_id},
        )
    items = res.json().get("items", [])
    return items[0]["id"] if items else None


# ── GET /billing/plans ────────────────────────────────────────────────────────

@router.get("/plans")
async def get_plans():
    return {
        "plans": [
            {
                "id": "starter",
                "name": "Starter",
                "price": 9,
                "price_id": PRODUCT_MAP["starter"],
                "features": [
                    "5 deployments/month",
                    "2 projects",
                    "Community support",
                ],
                "deployments_limit": 5,
                "projects_limit": 2,
                "priority": "normal",
            },
            {
                "id": "pro",
                "name": "Pro",
                "price": 29,
                "price_id": PRODUCT_MAP["pro"],
                "features": [
                    "50 deployments/month",
                    "10 projects",
                    "Priority support",
                ],
                "deployments_limit": 50,
                "projects_limit": 10,
                "priority": "high",
            },
            {
                "id": "team",
                "name": "Team",
                "price": 99,
                "price_id": PRODUCT_MAP["team"],
                "features": [
                    "Unlimited deployments",
                    "Unlimited projects",
                    "Dedicated support",
                ],
                "deployments_limit": -1,
                "projects_limit": -1,
                "priority": "priority",
            },
        ]
    }


# ── POST /billing/checkout ────────────────────────────────────────────────────

class CheckoutRequest(BaseModel):
    plan_id: str
    user_id: str
    success_url: str
    cancel_url: str


@router.get("/debug")
async def debug_billing():
    """Debug endpoint to check Polar configuration"""
    return {
        "polar_token_set": bool(POLAR_TOKEN),
        "polar_token_prefix": POLAR_TOKEN[:10] + "..." if POLAR_TOKEN else None,
        "product_ids": {
            "starter": PRODUCT_MAP["starter"][:8] + "..." if PRODUCT_MAP["starter"] else None,
            "pro": PRODUCT_MAP["pro"][:8] + "..." if PRODUCT_MAP["pro"] else None,
            "team": PRODUCT_MAP["team"][:8] + "..." if PRODUCT_MAP["team"] else None,
        },
        "webhook_secret_set": bool(POLAR_WEBHOOK_SECRET)
    }


@router.post("/checkout")
async def create_checkout(req: CheckoutRequest):
    product_id = PRODUCT_MAP.get(req.plan_id)
    if not product_id:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid plan_id '{req.plan_id}' or product ID not configured"
        )

    if not POLAR_TOKEN:
        raise HTTPException(
            status_code=500,
            detail="Polar access token not configured"
        )

    async with httpx.AsyncClient(timeout=30.0) as client:
        try:
            res = await client.post(
                f"{POLAR_API}/checkouts",
                headers=polar_headers(),
                json={
                    "products": [product_id],
                    "success_url": req.success_url,
                    "external_customer_id": req.user_id,
                    "metadata": {
                        "user_id": req.user_id,
                        "plan_id": req.plan_id,
                    },
                },
            )
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Polar API error: {str(e)}")

    if res.status_code != 201:
        try:
            error_data = res.json()
            raise HTTPException(
                status_code=400,
                detail=f"Polar checkout error: {error_data}"
            )
        except:
            raise HTTPException(
                status_code=400,
                detail=f"Polar checkout error: HTTP {res.status_code}"
            )

    data = res.json()
    return {
        "checkout_url": data["url"],
        "session_id": data["id"],
    }


# ── GET /billing/subscription/{user_id} ──────────────────────────────────────

@router.get("/subscription/{user_id}")
async def get_subscription(user_id: str):
    customer_id = await get_polar_customer_id(user_id)

    if not customer_id:
        return {
            "user_id": user_id,
            "plan": "free",
            "status": "active",
            "cancel_at_period_end": False,
            "current_period_start": None,
            "current_period_end": None,
            "polar_subscription_id": None,
        }

    async with httpx.AsyncClient() as client:
        subs_res = await client.get(
            f"{POLAR_API}/subscriptions",
            headers=polar_headers(),
            params={"customer_id": customer_id, "active": True},
        )

    subs = subs_res.json().get("items", [])

    if not subs:
        return {
            "user_id": user_id,
            "plan": "free",
            "status": "active",
            "cancel_at_period_end": False,
            "current_period_start": None,
            "current_period_end": None,
            "polar_subscription_id": None,
        }

    sub = subs[0]
    return {
        "user_id": user_id,
        "plan": product_to_plan(sub["product_id"]),
        "status": sub["status"],
        "cancel_at_period_end": sub.get("cancel_at_period_end", False),
        "current_period_start": sub.get("current_period_start"),
        "current_period_end": sub.get("current_period_end"),
        "polar_subscription_id": sub["id"],
    }


# ── POST /billing/cancel-subscription ────────────────────────────────────────

class CancelSubscriptionRequest(BaseModel):
    subscription_id: str


@router.post("/cancel-subscription")
async def cancel_subscription(req: CancelSubscriptionRequest):
    async with httpx.AsyncClient() as client:
        res = await client.delete(
            f"{POLAR_API}/subscriptions/{req.subscription_id}",
            headers=polar_headers(),
        )

    if res.status_code not in (200, 204):
        raise HTTPException(
            status_code=400,
            detail=f"Failed to cancel subscription: {res.json()}",
        )

    return {"status": "canceled"}


# ── POST /billing/portal-session ─────────────────────────────────────────────

class PortalSessionRequest(BaseModel):
    user_id: str
    return_url: str


@router.post("/portal-session")
async def create_portal_session(req: PortalSessionRequest):
    customer_id = await get_polar_customer_id(req.user_id)

    if not customer_id:
        raise HTTPException(
            status_code=404,
            detail="No Polar customer found for this user. "
                   "They may not have subscribed yet.",
        )

    async with httpx.AsyncClient() as client:
        session_res = await client.post(
            f"{POLAR_API}/customer-sessions",
            headers=polar_headers(),
            json={"customer_id": customer_id},
        )

    if session_res.status_code != 201:
        raise HTTPException(
            status_code=400,
            detail=f"Failed to create portal session: {session_res.json()}",
        )

    token = session_res.json().get("token")
    return {
        "portal_url": f"https://polar.sh/customer-portal?token={token}"
    }


# ── POST /billing/webhook ─────────────────────────────────────────────────────

def verify_signature(body: bytes, signature: str) -> bool:
    if not POLAR_WEBHOOK_SECRET:
        return False
    expected = hmac.new(
        POLAR_WEBHOOK_SECRET.encode(),
        body,
        hashlib.sha256,
    ).hexdigest()
    return hmac.compare_digest(expected, signature)


async def handle_event(event_type: str, data: dict):
    """
    Process webhook events in the background.
    Replace the TODO comments with your actual database calls.
    """
    try:
        user_id = data.get("customer", {}).get("external_id")

        if event_type == "subscription.active":
            product_id = data.get("product_id")
            plan = product_to_plan(product_id)
            subscription_id = data.get("id")
            print(f"✅ ACTIVE  — user={user_id}, plan={plan}, sub_id={subscription_id}")
            # TODO: await db.update_user_plan(user_id, plan, subscription_id)

        elif event_type == "subscription.updated":
            status = data.get("status")
            cancel_at_period_end = data.get("cancel_at_period_end", False)
            print(f"🔄 UPDATED — user={user_id}, status={status}, "
                  f"cancel_at_end={cancel_at_period_end}")
            # TODO: await db.sync_subscription(user_id, status, cancel_at_period_end)

        elif event_type == "subscription.canceled":
            ends_at = data.get("current_period_end")
            print(f"⏳ CANCELED — user={user_id}, access until={ends_at}")
            # TODO: await db.mark_canceling(user_id, ends_at)

        elif event_type == "subscription.revoked":
            print(f"🚫 REVOKED — user={user_id}")
            # TODO: await db.revoke_access(user_id)

        elif event_type == "order.created":
            billing_reason = data.get("billing_reason")
            if billing_reason == "subscription_cycle":
                print(f"💰 RENEWED — user={user_id}")
                # TODO: await db.log_renewal(user_id)

        else:
            print(f"ℹ️  UNHANDLED EVENT — type={event_type}")
            
    except Exception as e:
        # Log errors but don't raise them to prevent webhook failures
        print(f"⚠️ Error processing event {event_type}: {e}")


@router.post("/webhook")
async def polar_webhook(request: Request, background_tasks: BackgroundTasks):
    """
    Handle Polar webhooks.
    Always returns 200 to prevent Polar from retrying.
    """
    try:
        body = await request.body()
        signature = request.headers.get("webhook-signature", "")

        if not verify_signature(body, signature):
            print("⚠️ Invalid webhook signature")
            raise HTTPException(status_code=403, detail="Invalid webhook signature")

        event = await request.json()
        event_type = event.get("type", "")
        data = event.get("data", {})

        # List of events we actively process
        handled_events = {
            "subscription.active",
            "subscription.updated", 
            "subscription.canceled",
            "subscription.revoked",
            "order.created",
        }

        if event_type in handled_events:
            # Process known events in background
            background_tasks.add_task(handle_event, event_type, data)
            print(f"✅ Webhook received: {event_type}")
        else:
            # Log unhandled events but don't process them
            print(f"ℹ️  Unhandled Polar event: {event_type}")
            # Still return 200 so Polar stops retrying

        # Always return 200 so Polar stops retrying
        return {"received": True, "event_type": event_type}
        
    except HTTPException:
        raise
    except Exception as e:
        # Log error but still return 200 to prevent retries
        print(f"⚠️ Webhook processing error: {e}")
        return {"received": True, "error": str(e)}
