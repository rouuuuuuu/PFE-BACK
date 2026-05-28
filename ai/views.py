from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.conf import settings
from groq import Groq
import json
from backhaul.models import BackhaulLink


# ════════════════════════════════════════════
#  OPTION 2 — Alarm Analysis
# ════════════════════════════════════════════

def alarm_analysis_view(request):

    # Pull data from your DB
    total_links = BackhaulLink.objects.count()

    # This count is lightweight, so we can keep it for all links
    alarm_counts = {}
    for link in BackhaulLink.objects.values('alarm_severity'):
        sev = link['alarm_severity']
        alarm_counts[sev] = alarm_counts.get(sev, 0) + 1

    # FIX: Add [:20] to limit the payload sent to Groq and avoid 413 token errors
    alarmed = list(
        BackhaulLink.objects.exclude(alarm_severity='Normal').values(
            'alarm_severity', 'link_name',
            'source_ne', 'source_ip',
            'sink_ne', 'sink_ip',
            'link_level'
        )[:20] 
    )

    # Call Groq
    client = Groq(api_key=settings.GROQ_API_KEY)

    prompt = f"""You are a telecom NOC expert analyzing an ISIS optical network in Tunisia.

NETWORK HEALTH SUMMARY:
- Total monitored links: {total_links}
- Alarm breakdown: {alarm_counts}

ACTIVE ALARMS (Showing max 20 sample alarms due to payload limits):
{json.dumps(alarmed, indent=2)}

Provide a structured analysis:
1. SITUATION SUMMARY: Plain-English description
2. AFFECTED NODES: Which routers/corridors and their role (PE, UPE, POP...)
3. ROOT CAUSE HYPOTHESIS: Most likely cause
4. RECOMMENDED ACTIONS: Priority steps for the on-call engineer
5. NETWORK HEALTH: Overall assessment and risk level

Be concise and technical. Reader is a senior NOC engineer."""

    response = client.chat.completions.create(
        model="llama-3.3-70b-versatile",
        messages=[
            {
                "role": "system",
                "content": "You are an expert telecom NOC analyst specializing in MPLS/ISIS networks."
            },
            {
                "role": "user",
                "content": prompt
            }
        ],
        max_tokens=1000,
        temperature=0.3
    )

    return JsonResponse({
        "analysis": response.choices[0].message.content,
        "total_links": total_links,
        "alarm_counts": alarm_counts,
        "active_alarms_count": len(alarmed)  # Note: This will max out at 20 now
    })


# ════════════════════════════════════════════
#  OPTION 3 — NLP Provisioning
# ════════════════════════════════════════════

PROVISIONING_SYSTEM_PROMPT = """You are a telecom provisioning assistant for a Tunisian ISP.
Extract network service parameters from the engineer's request.
Return ONLY valid JSON. No explanation. No markdown. No backticks.
Use null for missing fields.

JSON schema:
{
  "client_name": "string or null",
  "pe_ip": "string or null",
  "vlan_id": "integer or null",
  "bandwidth_mbps": "integer or null",
  "service_type": "Internet or MPLS or Port reservation or VoiP or Bandwidth upgrade or null",
  "notes": "string or null"
}"""

@csrf_exempt
def nlp_provisioning_view(request):
    if request.method != "POST":
        return JsonResponse({"error": "POST only"}, status=405)

    body = json.loads(request.body)
    user_text = body.get("text", "").strip()

    if not user_text:
        return JsonResponse({"error": "No text provided"}, status=400)

    client = Groq(api_key=settings.GROQ_API_KEY)

    response = client.chat.completions.create(
        model="llama-3.3-70b-versatile",
        messages=[
            {
                "role": "system",
                "content": PROVISIONING_SYSTEM_PROMPT
            },
            {
                "role": "user",
                "content": user_text
            }
        ],
        max_tokens=300,
        temperature=0.0
    )

    raw = response.choices[0].message.content.strip()
    raw = raw.replace("```json", "").replace("```", "").strip()

    try:
        params = json.loads(raw)
        return JsonResponse({"params": params, "status": "ok"})
    except json.JSONDecodeError:
        return JsonResponse({"error": "Failed to parse response"}, status=500)
