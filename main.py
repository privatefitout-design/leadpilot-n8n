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

        logger.info(f"[TEST] Processing lead for {phone}")

        analysis = await analyze_transcript(transcript)

        analysis["phone"] = phone
        analysis["name"] = name
        analysis["source"] = "manual_test"

        logger.info(f"[TEST] lead_type: {analysis.get('lead_type')}")
        logger.info(f"[TEST] template: {analysis.get('whatsapp_template')}")

        await send_to_n8n(analysis)

        return {
            "status": "ok",
            "lead_type": analysis.get("lead_type"),
            "template": analysis.get("whatsapp_template"),
            "phone": phone,
            "name": name
        }

    except Exception as e:
        logger.error(f"test-lead error: {e}")
        return JSONResponse(status_code=500, content={"error": str(e)})
