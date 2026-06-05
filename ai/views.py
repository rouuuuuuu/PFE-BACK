import re
import json
import traceback
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods
from devices.models import Router, Switch, Port, Card, SFP
from backhaul.models import BackhaulLink

# ─────────────────────────────────────────────────────────────
#  Utility function to clean and extract Node Name
# ─────────────────────────────────────────────────────────────
def extract_node_name(query_text):
    """
    Robust extraction that isolates any substring matching telecom node identifiers.
    Matches formats like 1001-TUN0005, ARI_0008, PE1, etc., anywhere in the string.
    """
    # 1. Look for custom structured nodes with digits and dashes/underscores (e.g., 1001-TUN0005 or ARI_0008)
    complex_pattern = re.search(r'([a-z0-9]+[_-][a-z0-9_-]+)', query_text, re.IGNORECASE)
    if complex_pattern:
        return complex_pattern.group(1).upper()
        
    # 2. Look for short standard interface targets like PE1, PE2, etc.
    pe_pattern = re.search(r'\b(pe\d+)\b', query_text, re.IGNORECASE)
    if pe_pattern:
        return pe_pattern.group(1).upper()
        
    # 3. Intelligent Fallback if no dashes are present
    words = query_text.upper().split()
    exclude = ["SLOT", "CARTE", "PANNE", "BOARD", "CARD", "SPARE", "REMPLACEMENT", "POUR", "SUR", "RESEAU", "PORTS"]
    for word in words:
        # Clean potential punctuation attached to the word
        clean_word = re.sub(r'[^\w-]', '', word)
        # If the word contains numbers and isn't a restricted instruction word, it's our node
        if any(char.isdigit() for char in clean_word) and clean_word not in exclude:
            return clean_word
            
    return None
# ─────────────────────────────────────────────────────────────
#  Capability 1: Native Predictive SFP Analysis Engine
# ─────────────────────────────────────────────────────────────
def analyze_sfp_health_local():
    sfps = SFP.objects.all()
    critical_cases = []
    warning_cases = []
    
    for sfp in sfps:
        try:
            rx = float(sfp.rx_power) if sfp.rx_power is not None else 0.0 
        except (ValueError, TypeError):
            continue 
        
        if rx <= -38.0:
            critical_cases.append(sfp)
        elif rx <= -30.0:
            warning_cases.append(sfp)
            
    output = ["### <i class='fas fa-search' style='color: #ff9800;'></i> Native Predictive SFP Failure Analysis\n"]
    if not critical_cases and not warning_cases:
        output.append("<i class='fas fa-check-circle' style='color: #4caf50;'></i> All SFP optical power levels are within operational baselines.")
        return "\n".join(output)
        
    if critical_cases:
        output.append("<i class='fas fa-times-circle' style='color: #f44336;'></i> **CRITICAL DEGRADATION DETECTED (<= -38 dBm):**")
        for s in critical_cases[:10]:
            output.append(
                f"- Node `{s.ne_name}` | Port `{s.port_name}` | Rx: **{s.rx_power} dBm**\n"
                f"  *Recommendation:* Immediate SFP replacement required. Inspect fiber continuity for macro-cuts."
            )
            
    if warning_cases:
        output.append("\n<i class='fas fa-exclamation-triangle' style='color: #ff9800;'></i> **WARNING - HIGH OPTICAL ATTENUATION (-30 dBm to -37 dBm):**")
        for s in warning_cases[:10]:
            output.append(
                f"- Node `{s.ne_name}` | Port `{s.port_name}` | Rx: **{s.rx_power} dBm**\n"
                f"  *Recommendation:* High insertion loss detected. Schedule a maintenance window to clean connectors."
            )
            
    return "\n".join(output)

# ─────────────────────────────────────────────────────────────
#  Capability 2: Native Spare Parts Reallocation Algorithm
# ─────────────────────────────────────────────────────────────
def find_spare_parts_local(faulty_node, faulty_slot):
    if not faulty_node:
        return "<i class='fas fa-exclamation-circle' style='color: #ff9800;'></i> Router hostname not recognized. Example: *Card failure on slot 3 of ARI_0010*"

    failed_card = Card.objects.filter(ne_name__icontains=faulty_node, slot_id=faulty_slot).first()
    
    if not failed_card:
        node_exists = Card.objects.filter(ne_name__icontains=faulty_node).exists()
        if node_exists:
            return f"<i class='fas fa-times-circle' style='color: #f44336;'></i> No hardware card found registered on **slot {faulty_slot}** for `{faulty_node}` (Node exists in repository)."
        return f"<i class='fas fa-times-circle' style='color: #f44336;'></i> Target node `{faulty_node}` could not be found in hardware inventory."

    board_type = failed_card.board_type
    matching_cards = Card.objects.filter(board_type=board_type).exclude(ne_name__icontains=faulty_node)
    
    output = [
        f"### <i class='fas fa-boxes' style='color: #2196f3;'></i> Smart Spare Parts Management",
        f"- **Faulty Board Type:** `{board_type}` ({failed_card.description}) on `{failed_card.ne_name}` (Slot {faulty_slot})\n"
    ]
    
    if matching_cards.exists():
        output.append(f"<i class='fas fa-network-wired' style='color: #00bcd4;'></i> **Available Reallocation Candidates ({matching_cards.count()} units found):**")
        for card in matching_cards[:5]:
            output.append(f"  * Node `{card.ne_name}` | Slot `{card.slot_id}`")
        output.append(f"\n<i class='fas fa-lightbulb' style='color: #ffeb3b;'></i> **Recommendation:** Consider temporary hardware reallocation from non-critical paths listed above.")
    else:
        output.append(f"<i class='fas fa-exclamation-triangle' style='color: #ff9800;'></i> No identical replacement modules found in active production items.\n"
                      f"<i class='fas fa-lightbulb' style='color: #ffeb3b;'></i> **Recommendation:** Open procurement request from central warehouse storage buffers immediately.")
                      
    return "\n".join(output)

# ─────────────────────────────────────────────────────────────
#  The Core Chat Engine
# ─────────────────────────────────────────────────────────────
@csrf_exempt
@require_http_methods(["POST"])
def ai_chat_local_scratch(request):
    try:
        body = json.loads(request.body)
        messages = body.get("messages", [])
        if not messages:
            return JsonResponse({"error": "No messages provided."}, status=400)
            
        user_query = messages[-1].get("content", "")
        user_query_lower = user_query.lower()
        
        # 1. SFP & Optical Diagnostics
        if re.search(r'(sfp|optics|optical|rx_power|laser|fibre|optique|rx)', user_query_lower):
            response_text = analyze_sfp_health_local()
            
        # 2. Hardware Slot / Card Failures
        elif re.search(r'(board|slot|card|carte|spare|panne|remplacement|failure)', user_query_lower):
            slot_match = re.search(r'slot\s*(\d+)', user_query_lower)
            slot = slot_match.group(1) if slot_match else "3"
            
            node = extract_node_name(user_query)
            response_text = find_spare_parts_local(node, slot)
            
        # 3. Dynamic Node Port Checking
        elif re.search(r'port', user_query_lower) and any(x in user_query_lower for x in ['sur', 'pour', 'on', 'for', 'node', 'router', 'routeur']):
            node_name = extract_node_name(user_query)
            
            if not node_name:
                response_text = "<i class='fas fa-exclamation-circle' style='color: #ff9800;'></i> Unable to extract target hostname. Format sample: *Ports down on ARI_0008*"
            else:
                count_down = Port.objects.filter(ne_name__icontains=node_name, oper_status__iexact="down").count()
                count_up = Port.objects.filter(ne_name__icontains=node_name, oper_status__iexact="up").count()
                total = count_down + count_up
                
                if total == 0:
                    all_distinct_nodes = list(Port.objects.values_list('ne_name', flat=True).distinct()[:3])
                    response_text = (
                        f"<i class='fas fa-info-circle' style='color: #00e5ff;'></i> No active interface ports matched for query string `{node_name}`.\n\n"
                        f"**Debugging note:** Current inventory contains patterns like: `{all_distinct_nodes}`"
                    )
                else:
                    response_text = (
                        f"### <i class='fas fa-chart-bar' style='color: #e040fb;'></i> Interface Port Status Summary for `{node_name}`\n"
                        f"- <i class='fas fa-circle' style='color: #f44336; font-size: 10px; vertical-align: middle; margin-right: 6px;'></i> Operational State **DOWN** : **{count_down}**\n"
                        f"- <i class='fas fa-circle' style='color: #4caf50; font-size: 10px; vertical-align: middle; margin-right: 6px;'></i> Operational State **UP** : **{count_up}**\n"
                        f"- <i class='fas fa-calculator' style='color: #9e9e9e; margin-right: 6px;'></i> Total Tracked Ports: **{total}**"
                    )

        # 4. Global Network Summary
        elif re.search(r'(status|health|summary|down|critical|combien|état|resume|réseau|network)', user_query_lower):
            down_ports = Port.objects.filter(oper_status__iexact="down").count()
            crit_links = BackhaulLink.objects.filter(alarm_severity="Critical").count()
            response_text = (
                f"### <i class='fas fa-tachometer-alt' style='color: #00e5ff;'></i> Global Core Network Status\n"
                f"- <i class='fas fa-circle' style='color: #f44336; font-size: 10px; vertical-align: middle; margin-right: 6px;'></i> Total Interfaces Marked **DOWN**: **{down_ports}**\n"
                f"- <i class='fas fa-bolt' style='color: #ff9800; margin-right: 6px;'></i> Operational Backhaul Links with **Critical Alarms**: **{crit_links}**"
            )
        else:
            response_text = (
                "👋 Iris Here! Please supply a targeted infrastructure query:\n\n"
                "1. **Core Status Summary:** (e.g., 'network health statement')\n"
                "2. **Optical Metrics Diagnostics:** (e.g., 'check sfp lasers')\n"
                "3. **Spare Hardware Relocations:** (e.g., 'failed board on slot 2 of node ARI_0008')\n"
                "4. **Per-Node Interface Statistics:** (e.g., 'show port configuration status on PE1')"
            )

        return JsonResponse({"response": response_text})

    except Exception as e:
        print("\n❌ CORE CHATBOT PROCESSING FAULT:")
        traceback.print_exc()
        return JsonResponse({"error": str(e)}, status=500)

# ─────────────────────────────────────────────────────────────
#  Quick-stats endpoint
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
