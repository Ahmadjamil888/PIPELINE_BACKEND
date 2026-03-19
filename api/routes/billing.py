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


@router.post("/checkout")
async def create_checkout(req: CheckoutRequest):
    product_id = PRODUCT_MAP.get(req.plan_id)
    if not product_id:
        raise HTTPException(status_code=400, detail=f"Invalid plan_id: {req.plan_id}")

    async with httpx.AsyncClient() as client:
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

    if res.status_code != 201:
        raise HTTPException(
            status_code=400,
            detail=f"Polar checkout error: {res.json()}",
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


async def handle_event(event_type: str, data: dict, db=None):
    """
    Process webhook events in the background.
    Updates subscriptions table in Supabase.
    """
    import os
    from supabase import create_client
    
    # Initialize Supabase client if db not provided
    if db is None:
        supabase_url = os.getenv("SUPABASE_URL")
        supabase_key = os.getenv("SUPABASE_SERVICE_KEY")
        if supabase_url and supabase_key:
            db = create_client(supabase_url, supabase_key)
    
    user_id = data.get("customer", {}).get("external_id")
    if not user_id:
        print(f"⚠️ No user_id found in webhook data")
        return
    
    if event_type == "subscription.active":
        product_id = data.get("product_id")
        plan = product_to_plan(product_id)
        subscription_id = data.get("id")
        current_period_start = data.get("current_period_start")
        current_period_end = data.get("current_period_end")
        
        print(f"✅ ACTIVE  — user={user_id}, plan={plan}, sub_id={subscription_id}")
        
        # Upsert subscription
        if db:
            try:
                db.table("subscriptions").upsert({
                    "profile_id": user_id,
                    "plan": plan,
                    "polar_subscription_id": subscription_id,
                    "status": "active",
                    "current_period_start": current_period_start,
                    "current_period_end": current_period_end,
                    "cancel_at_period_end": False,
                }, on_conflict="profile_id").execute()
                print(f"  → Subscription upserted for {user_id}")
            except Exception as e:
                print(f"  → Error upserting subscription: {e}")
    
    elif event_type == "subscription.updated":
        status = data.get("status")
        cancel_at_period_end = data.get("cancel_at_period_end", False)
        
        print(f"🔄 UPDATED — user={user_id}, status={status}, cancel_at_end={cancel_at_period_end}")
        
        if db:
            try:
                db.table("subscriptions").update({
                    "status": status,
                    "cancel_at_period_end": cancel_at_period_end,
                }).eq("profile_id", user_id).execute()
                print(f"  → Subscription updated for {user_id}")
            except Exception as e:
                print(f"  → Error updating subscription: {e}")
    
    elif event_type == "subscription.canceled":
        ends_at = data.get("current_period_end")
        
        print(f"⏳ CANCELED — user={user_id}, access until={ends_at}")
        
        if db:
            try:
                db.table("subscriptions").update({
                    "status": "canceled",
                    "cancel_at_period_end": True,
                    "current_period_end": ends_at,
                }).eq("profile_id", user_id).execute()
                print(f"  → Subscription marked as canceling for {user_id}")
            except Exception as e:
                print(f"  → Error updating subscription: {e}")
    
    elif event_type == "subscription.revoked":
        print(f"🚫 REVOKED — user={user_id}")
        
        if db:
            try:
                db.table("subscriptions").update({
                    "status": "revoked",
                }).eq("profile_id", user_id).execute()
                print(f"  → Subscription revoked for {user_id}")
            except Exception as e:
                print(f"  → Error updating subscription: {e}")
    
    elif event_type == "order.created":
        billing_reason = data.get("billing_reason")
        if billing_reason == "subscription_cycle":
            print(f"💰 RENEWED — user={user_id}")
            # Renewal is handled by subscription.updated event
    
    else:
        print(f"ℹ️  UNHANDLED EVENT — type={event_type}")


@router.post("/webhook")
async def polar_webhook(request: Request, background_tasks: BackgroundTasks):
    body = await request.body()
    signature = request.headers.get("webhook-signature", "")

    if not verify_signature(body, signature):
        raise HTTPException(status_code=403, detail="Invalid webhook signature")

    event = await request.json()
    event_type = event.get("type", "")
    data = event.get("data", {})

    # Acknowledge immediately, process in background
    # This prevents Polar from timing out and disabling your endpoint
    background_tasks.add_task(handle_event, event_type, data)

    return {"received": True}
