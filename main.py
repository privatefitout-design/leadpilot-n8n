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


def get_fallback():
    return {
        "lead_type": "neutral",
        "intent_level": "uncertain",
        "pain_point": "",
        "whatsapp_template": "neutral_followup",
        "call_summary": "analysis failed",
        "recommended_next_step": "send neutral message",
    }


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
            "content-type": "application/json",
        }

        payload = {
            "model": "claude-3-haiku-20240307",
            "max_tokens": 300,
            "temperature": 0.2,
            "messages": [
                {"role": "user", "content": prompt}
            ],
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
        except Exception:
            logger.error("Failed to parse Anthropic JSON")
            return get_fallback()

    except Exception as e:
        logger.error(f"Anthropic exception: {e}")
        return get_fallback()


async def send_to_n8n(data: dict):
    logger.info(f"Sending to n8n URL: {N8N_WEBHOOK_URL}")
    logger.info(f"Payload: {json.dumps(data, ensure_ascii=False)}")

    if DRY_RUN:
        logger.info("[DRY RUN] skipping n8n send")
        return True

    if not N8N_WEBHOOK_URL:
        logger.warning("N8N_WEBHOOK_URL missing")
        return False

    try:
        async with httpx.AsyncClient(timeout=20.0) as client:
            response = await client.post(N8N_WEBHOOK_URL, json=data)
            logger.info(f"n8n status: {response.status_code}")
            logger.info(f"n8n response: {response.text}")
            return response.status_code in (200, 201)
    except Exception as e:
        logger.error(f"Failed to send to n8n: {e}")
        return False


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.post("/webhook/elevenlabs")
async def webhook_elevenlabs(request: Request):
    try:
        body = await request.json()

        transcript = ""
        data = body.get("data", body) if isinstance(body, dict) else {}

        if isinstance(data, dict):
            if "transcript" in data:
                transcript = str(data["transcript"])
            elif "messages" in data:
                transcript = "\n".join(
                    f"{m.get('role', '')}: {m.get('content', '')}"
                    for m in data["messages"]
                )
            elif "text" in data:
                transcript = str(data["text"])

        if len(transcript.strip()) < 10:
            return {"status": "ignored", "reason": "short_transcript"}

        analysis = await analyze_transcript(transcript)

        phone = body.get("phone") or body.get("from")
        name = body.get("name")

        if not phone:
            return {"status": "error", "reason": "no_phone"}

        analysis["phone"] = phone
        analysis["name"] = name
        analysis["source"] = "elevenlabs"

        await send_to_n8n(analysis)

        return {
            "status": "ok",
            "lead_type": analysis.get("lead_type"),
            "template": analysis.get("whatsapp_template"),
            "dry_run": DRY_RUN,
        }

    except Exception as e:
        logger.error(f"elevenlabs webhook error: {e}")
        return JSONResponse(status_code=500, content={"error": str(e)})


@app.post("/test-lead")
async def test_lead(request: Request):
    try:
        body = await request.json()

        phone = body.get("phone")
        name = body.get("name")
        transcript = body.get("transcript", "")

        if not phone:
            return {"status": "error", "reason": "no_phone"}

        if len(transcript.strip()) < 10:
            return {"status": "ignored", "reason": "short_transcript"}

        analysis = await analyze_transcript(transcript)

        analysis["phone"] = phone
        analysis["name"] = name
        analysis["source"] = "manual_test"

        logger.info(f"[TEST] phone: {phone}")
        logger.info(f"[TEST] lead_type: {analysis.get('lead_type')}")
        logger.info(f"[TEST] template: {analysis.get('whatsapp_template')}")

        await send_to_n8n(analysis)

        return {
            "status": "ok",
            "lead_type": analysis.get("lead_type"),
            "template": analysis.get("whatsapp_template"),
            "phone": phone,
            "name": name,
        }

    except Exception as e:
        logger.error(f"test-lead error: {e}")
        return JSONResponse(status_code=500, content={"error": str(e)})


@app.post("/webhook/twilio-call")
async def webhook_twilio_call(request: Request):
    try:
        form = await request.form()

        payload = {
            "source": "twilio_call",
            "call_sid": form.get("CallSid"),
            "call_status": form.get("CallStatus"),
            "from": form.get("From"),
            "to": form.get("To"),
            "direction": form.get("Direction"),
        }

        logger.info(f"Twilio callback: {json.dumps(payload, ensure_ascii=False)}")

        return {"status": "ok", "received": True}

    except Exception as e:
        logger.error(f"twilio webhook error: {e}")
        return JSONResponse(status_code=500, content={"error": str(e)})


if __name__ == "__main__":
    import uvicorn

    port = int(os.getenv("PORT", 8080))
    uvicorn.run(app, host="0.0.0.0", port=port)
