from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
import logging
import os
import json
import httpx

app = FastAPI(title="LeadPilot Backend")

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY")
N8N_WEBHOOK_URL = os.getenv("N8N_WEBHOOK_URL")
DRY_RUN = os.getenv("DRY_RUN", "false").lower() == "true"


# ====================== ANALYSIS (ANTHROPIC) ======================
async def analyze_transcript(transcript: str) -> dict:
    if not ANTHROPIC_API_KEY:
        logger.error("ANTHROPIC_API_KEY not set")
        return get_fallback()

    try:
        url = "https://api.anthropic.com/v1/messages"

        prompt = f"""
You are a lead qualification AI.

Analyze this call transcript and return ONLY valid JSON.

Classification:
- warm
- neutral
- cold

Return strictly:

{{
  "lead_type": "",
  "intent_level": "",
  "pain_point": "",
  "whatsapp_template": "",
  "call_summary": "",
  "recommended_next_step": ""
}}

Transcript:
{transcript}
"""

        headers = {
            "x-api-key": ANTHROPIC_API_KEY,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json"
        }

        payload = {
            "model": "claude-3-haiku-20240307",
            "max_tokens": 300,
            "temperature": 0.2,
            "messages": [
                {"role": "user", "content": prompt}
            ]
        }

        async with httpx.AsyncClient(timeout=20) as client:
            resp = await client.post(url, headers=headers, json=payload)

        if resp.status_code != 200:
            logger.error(f"Anthropic error: {resp.text}")
            return get_fallback()

        data = resp.json()
        content = data["content"][0]["text"].strip()

        if content.startswith("```"):
            content = content.split("```")[1].strip()
            if content.startswith("json"):
                content = content[4:].strip()

        try:
            return json.loads(content)
        except:
            return get_fallback()

    except Exception as e:
        logger.error(f"Anthropic exception: {e}")
        return get_fallback()


def get_fallback():
    return {
        "lead_type": "neutral",
        "intent_level": "uncertain",
        "pain_point": "",
        "whatsapp_template": "neutral_followup",
        "call_summary": "analysis failed",
        "recommended_next_step": "send neutral message"
    }


# ====================== SEND TO N8N ======================
async def send_to_n8n(data: dict):
    if DRY_RUN:
        logger.info(f"[DRY RUN] {data}")
        return True

    if not N8N_WEBHOOK_URL:
        logger.warning("N8N_WEBHOOK_URL missing")
        return False

    try:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.post(N8N_WEBHOOK_URL, json=data)
            return r.status_code in (200, 201)
    except Exception as e:
        logger.error(f"n8n error: {e}")
        return False


# ====================== ENDPOINT ======================
@app.post("/webhook/elevenlabs")
async def webhook(request: Request):
    try:
        body = await request.json()

        transcript = ""

        data = body.get("data", body)

        if "transcript" in data:
            transcript = data["transcript"]

        elif "messages" in data:
            transcript = "\n".join(
                f"{m.get('role')}: {m.get('content')}"
                for m in data["messages"]
            )

        if len(transcript.strip()) < 10:
            return {"status": "ignored"}

        analysis = await analyze_transcript(transcript)

        phone = body.get("phone") or body.get("from")
        name = body.get("name")

        if not phone:
            return {"status": "no_phone"}

        analysis["phone"] = phone
        analysis["name"] = name
        analysis["source"] = "elevenlabs"

        await send_to_n8n(analysis)

        return {
            "status": "ok",
            "lead_type": analysis["lead_type"],
            "template": analysis["whatsapp_template"],
            "dry_run": DRY_RUN
        }

    except Exception as e:
        logger.error(e)
        return JSONResponse(status_code=500, content={"error": str(e)})


@app.get("/health")
async def health():
    return {"status": "ok"}