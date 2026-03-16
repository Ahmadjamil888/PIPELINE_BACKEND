import asyncio, httpx, os
from dotenv import load_dotenv

load_dotenv()

# Use sandbox first to test, then swap to production URL
# POLAR_API = "https://sandbox-api.polar.sh/v1"
POLAR_API = "https://api.polar.sh/v1"  # Production

HEADERS = {
    "Authorization": f"Bearer {os.getenv('POLAR_ACCESS_TOKEN')}",
    "Content-Type": "application/json",
}

PLANS = [
    {"name": "Starter", "description": "5 deployments/mo, 2 projects", "price": 9},
    {"name": "Pro",     "description": "50 deployments/mo, 10 projects", "price": 29},
    {"name": "Team",    "description": "Unlimited deployments & projects", "price": 99},
]

async def seed():
    async with httpx.AsyncClient() as client:
        for plan in PLANS:
            res = await client.post(
                f"{POLAR_API}/products",
                headers=HEADERS,
                json={
                    "name": plan["name"],
                    "description": plan["description"],
                    "prices": [{
                        "type": "recurring",
                        "amount_type": "fixed",
                        "price_amount": plan["price"] * 100,
                        "price_currency": "usd",
                        "recurring_interval": "month",
                    }],
                },
            )
            data = res.json()
            if "id" in data:
                print(f"POLAR_PRODUCT_ID_{plan['name'].upper()}={data['id']}")
            else:
                print(f"ERROR creating {plan['name']}: {data}")

asyncio.run(seed())
