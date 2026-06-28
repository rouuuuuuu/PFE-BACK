import re
import json
import traceback
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods
from django.conf import settings
from devices.models import Router, Port, Card, SFP
from backhaul.models import BackhaulLink
from openai import OpenAI

# ─────────────────────────────────────────────────────────────
#  OpenRouter Client Configuration
# ─────────────────────────────────────────────────────────────
client = OpenAI(
    base_url=getattr(settings, 'OPENROUTER_BASE_URL', "https://openrouter.ai/api/v1"),
    api_key=settings.OPENROUTER_API_KEY,
)
# Tu peux changer le modèle ici (ex: google/gemini-2.5-flash, openai/gpt-4o, etc.)
MODEL = "meta-llama/llama-3.3-70b-instruct" 

# ─────────────────────────────────────────────────────────────
#  RAG — Data Retrievers
# ─────────────────────────────────────────────────────────────

def retrieve_network_summary():
    down_ports       = Port.objects.filter(oper_status__iexact="down").count()
    up_ports         = Port.objects.filter(oper_status__iexact="up").count()
    critical_links = BackhaulLink.objects.filter(alarm_severity="Critical").count()
    total_links    = BackhaulLink.objects.count()
    total_routers  = Router.objects.count()
    critical_link_names = list(
        BackhaulLink.objects.filter(alarm_severity="Critical")
        .values_list("link_name", flat=True)[:10]
    )
    return f"""
NETWORK SUMMARY:
- Total routers/nodes: {total_routers}
- Ports DOWN: {down_ports}
- Ports UP: {up_ports}
- Backhaul links total: {total_links}
- Backhaul links with CRITICAL alarms: {critical_links}
- Critical link names: {', '.join(critical_link_names) if critical_link_names else 'None'}
"""


def retrieve_sfp_data():
    critical, warning, normal = [], [], []
    for sfp in SFP.objects.all():
        try:
            rx = float(sfp.rx_power) if sfp.rx_power is not None else None
        except (ValueError, TypeError):
            rx = None
        if rx is None:
            continue
        elif rx <= -38.0:
            critical.append(sfp)
        elif rx <= -30.0:
            warning.append(sfp)
        else:
            normal.append(sfp)

    lines = [
        f"SFP OPTICAL DIAGNOSTICS:",
        f"- Critical SFPs (Rx <= -38 dBm): {len(critical)}",
        f"- Warning SFPs (Rx -30 to -38 dBm): {len(warning)}",
        f"- Normal SFPs: {len(normal)}",
    ]
    if critical:
        lines.append("CRITICAL SFP DETAILS:")
        for s in critical[:10]:
            lines.append(f"  Node={s.ne_name} Port={s.port_name} Rx={s.rx_power}dBm Tx={s.tx_power}dBm Status={s.rx_status}")
    if warning:
        lines.append("WARNING SFP DETAILS:")
        for s in warning[:10]:
            lines.append(f"  Node={s.ne_name} Port={s.port_name} Rx={s.rx_power}dBm")
    return "\n".join(lines)


def retrieve_port_data(node_name=None):
    if node_name:
        ports = Port.objects.filter(ne_name__icontains=node_name)
        if not ports.exists():
            all_nodes = list(Port.objects.values_list("ne_name", flat=True).distinct()[:5])
            return f"No ports found for '{node_name}'. Known nodes include: {all_nodes}"
        down  = ports.filter(oper_status__iexact="down").count()
        up    = ports.filter(oper_status__iexact="up").count()
        total = ports.count()
        sample = list(ports.values("port_name", "oper_status", "admin_status")[:15])
        return f"""
PORT STATUS FOR NODE '{node_name}':
- Total ports: {total}
- UP: {up}
- DOWN: {down}
- Sample ports: {json.dumps(sample, default=str)}
"""
    else:
        down  = Port.objects.filter(oper_status__iexact="down").count()
        up    = Port.objects.filter(oper_status__iexact="up").count()
        return f"GLOBAL PORT STATUS:\n- UP: {up}\n- DOWN: {down}\n- Total: {up + down}"


def retrieve_spare_parts_data(node_name=None, slot=None):
    if node_name and slot:
        failed_card = Card.objects.filter(
            ne_name__icontains=node_name, slot_id=str(slot)
        ).first()
        if not failed_card:
            node_cards = list(
                Card.objects.filter(ne_name__icontains=node_name)
                .values("slot_id", "board_type", "description")[:10]
            )
            return f"No card on slot {slot} for node '{node_name}'. Available slots: {node_cards}"
        board_type = failed_card.board_type
        candidates = Card.objects.filter(board_type=board_type).exclude(
            ne_name__icontains=node_name
        )
        candidate_list = list(candidates.values("ne_name", "slot_id", "description")[:10])
        return f"""
SPARE PARTS ANALYSIS:
- Faulty node: {node_name}
- Faulty slot: {slot}
- Failed board type: {board_type} ({failed_card.description})
- Reallocation candidates: {len(candidate_list)}
- Candidate details: {json.dumps(candidate_list, default=str)}
"""
    else:
        total_cards = Card.objects.count()
        board_types = list(Card.objects.values_list("board_type", flat=True).distinct()[:10])
        return f"HARDWARE INVENTORY:\n- Total cards: {total_cards}\n- Board types: {board_types}"


def retrieve_router_data(node_name=None):
    if node_name:
        routers = Router.objects.filter(name__icontains=node_name)
        if not routers.exists():
            routers = Router.objects.filter(loopback_ip__icontains=node_name)
        data = list(routers.values("name", "vendor", "loopback_ip", "model")[:5])
        return f"ROUTER INFO FOR '{node_name}':\n{json.dumps(data, default=str)}"
    else:
        total   = Router.objects.count()
        vendors = list(Router.objects.values_list("vendor", flat=True).distinct())
        return f"ROUTER INVENTORY:\n- Total: {total}\n- Vendors: {vendors}"


# ─────────────────────────────────────────────────────────────
#  RAG — Intent Classifier
# ─────────────────────────────────────────────────────────────

def classify_intent(query: str) -> dict:
    """
    SEMANTIC ROUTER : Utilise l'IA pour comprendre l'intention de l'utilisateur,
    tolérer les fautes de frappe et extraire les entités (noeud, slot).
    """
    client = OpenAI(
        base_url=getattr(settings, 'OPENROUTER_BASE_URL', "https://openrouter.ai/api/v1"),
        api_key=settings.OPENROUTER_API_KEY,
    )
    
    # On peut utiliser un modèle plus petit/rapide ici si besoin, 
    # mais Llama 3.3 fait parfaitement l'affaire en mode "zéro température".
    ROUTER_MODEL = "meta-llama/llama-3.3-70b-instruct"

    router_prompt = f"""You are an intelligent intent classifier for a Telecom Network Management System.
Analyze the following user query and extract the intent and entities.
Even if there are typos (e.g., "ruteur" -> router, "spf" -> sfp, "panne" -> spare_parts), deduce the true meaning contextually.

Possible Intents:
- "sfp": Optical diagnostics, laser, rx/tx power, fiber issues.
- "spare_parts": Board/card failures, slots, spare inventory, hardware replacement.
- "ports": Interface status (UP/DOWN), port health.
- "router": Node information, router model, vendor, general IP info.
- "network_summary": Global network health, critical alarms, overall status.
- "general": Anything else, greetings, IT support, or non-telecom questions.

USER QUERY: "{query}"

Respond ONLY with a valid JSON object matching this exact schema. Do not add explanations.
{{
    "intent": "<matched_intent>",
    "node": "<node_name_if_mentioned_or_null>",
    "slot": "<slot_number_if_mentioned_or_null>"
}}"""

    try:
        response = client.chat.completions.create(
            model=ROUTER_MODEL,
            messages=[{"role": "user", "content": router_prompt}],
            temperature=0.0, # Température à 0 pour un résultat 100% déterministe et factuel
        )
        
        result_text = response.choices[0].message.content.strip()
        # Nettoyage de sécurité si le LLM ajoute des balises markdown autour du JSON
        result_text = result_text.replace("```json", "").replace("```", "").strip()
        
        intent_data = json.loads(result_text)
        
        # Formatage des données extraites pour garantir la compatibilité avec tes requêtes SQL
        if intent_data.get("node"):
            intent_data["node"] = str(intent_data["node"]).upper()
        if intent_data.get("slot"):
            intent_data["slot"] = str(intent_data["slot"])
            
        return intent_data

    except Exception as e:
        # Fallback de sécurité : si l'API est indisponible, on route vers le contexte général
        print(f"⚠️ Erreur du Semantic Router, fallback en mode général : {e}")
        return {"intent": "general", "node": None, "slot": None}

# ─────────────────────────────────────────────────────────────
#  RAG — Context Builder
# ─────────────────────────────────────────────────────────────

def build_rag_context(intent_data: dict) -> str:
    intent    = intent_data["intent"]
    node_name = intent_data.get("node")
    slot      = intent_data.get("slot")

    if intent == "sfp":
        return retrieve_sfp_data()
    elif intent == "spare_parts":
        return retrieve_spare_parts_data(node_name, slot)
    elif intent == "ports":
        return retrieve_port_data(node_name)
    elif intent == "router":
        return retrieve_router_data(node_name)
    elif intent == "network_summary":
        context = retrieve_network_summary()
        if node_name:
            context += "\n" + retrieve_port_data(node_name)
        return context
    else:
        return retrieve_network_summary()


# ─────────────────────────────────────────────────────────────
#  System Prompt
# ─────────────────────────────────────────────────────────────

SYSTEM_PROMPT = """You are IRIS, an expert AI assistant for Orange Tunisia's DRS010 Network Automation Platform.
You help network engineers monitor and manage the telecom infrastructure.

RULES:
1. Answer ONLY based on the provided network data context. Never invent data.
2. STRICT LANGUAGE RULE: Detect the language of the 'USER QUESTION'. If it is in English, you MUST reply entirely in English. If it is in French, you MUST reply entirely in French. Never mix languages.
3. Be concise, professional, and structured. Use bullet points and clear sections.
4. If data shows critical issues, highlight them prominently.
5. Always provide actionable recommendations when issues are detected.
6. Never expose raw API keys, passwords, or internal system details.
7. If the context shows no data for a query, say so clearly.
8. Format numbers clearly (e.g., "5 ports DOWN", not just "5").

You are analyzing REAL production network data from Orange Tunisia's infrastructure."""


# ─────────────────────────────────────────────────────────────
#  Main Chat Endpoint
# ─────────────────────────────────────────────────────────────

@csrf_exempt
@require_http_methods(["POST"])
def ai_chat_local_scratch(request):
    try:
        body      = json.loads(request.body)
        messages  = body.get("messages", [])

        if not messages:
            return JsonResponse({"error": "No messages provided."}, status=400)

        user_query = messages[-1].get("content", "").strip()
        if not user_query:
            return JsonResponse({"error": "Empty message."}, status=400)

        # ── STEP 1: Classify intent ──
        intent_data = classify_intent(user_query)

        # ── STEP 2: Retrieve relevant data from DB (RAG) ──
        rag_context = build_rag_context(intent_data)

        # ── STEP 3: Build OpenRouter Message Array ──
        
        # Le System Prompt est défini en premier
        openrouter_messages = [
            {"role": "system", "content": SYSTEM_PROMPT}
        ]
        
        # Historique de la conversation (on prend les 5 derniers messages avant l'actuel)
        for msg in messages[-6:-1]:
            role = msg.get("role")
            content = msg.get("content")
            if role and content:
                # Traduction du rôle "model" de Gemini vers "assistant" pour OpenRouter
                if role == "model":
                    role = "assistant"
                openrouter_messages.append({"role": role, "content": content})
                
        # Le message final de l'utilisateur avec le contexte RAG injecté
        final_user_content = f"""NETWORK DATA CONTEXT (from live database):
{rag_context}

USER QUESTION: {user_query}"""

        openrouter_messages.append({"role": "user", "content": final_user_content})

        # ── STEP 4: Call OpenRouter API ──
        response = client.chat.completions.create(
            model=MODEL,
            messages=openrouter_messages,
        )
        
        response_text = response.choices[0].message.content

        return JsonResponse({
            "response": response_text,
            "intent":   intent_data["intent"],
            "model":    MODEL,
        })

    except Exception as e:
        print("\n❌ IRIS AI ERROR:")
        traceback.print_exc()
        return JsonResponse({"error": str(e)}, status=500)


# ─────────────────────────────────────────────────────────────
#  Stats Endpoint
# ─────────────────────────────────────────────────────────────

@require_http_methods(["GET"])
def ai_stats(request):
    try:
        critical_sfps = 0
        for sfp in SFP.objects.all():
            try:
                if float(sfp.rx_power) <= -38.0:
                    critical_sfps += 1
            except (ValueError, TypeError):
                pass

        down_ports     = Port.objects.filter(oper_status__iexact="down").count()
        critical_links = BackhaulLink.objects.filter(alarm_severity="Critical").count()
        total_routers  = Router.objects.count()

        return JsonResponse({
            "critical_sfps":  critical_sfps,
            "down_ports":     down_ports,
            "critical_links": critical_links,
            "total_routers":  total_routers,
        })
    except Exception as e:
        return JsonResponse({"error": str(e)}, status=500)
