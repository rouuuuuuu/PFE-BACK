import re
import json
import traceback
import difflib  # <-- NOUVEL IMPORT POUR LE FUZZY MATCHING
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods
from devices.models import Router, Switch, Port, Card, SFP
from backhaul.models import BackhaulLink

# ─────────────────────────────────────────────────────────────
#  1. Utility: Extract Node Name from Text
# ─────────────────────────────────────────────────────────────
def extract_node_name(query_text):
    """
    Extrait l'identifiant brut tapé par l'utilisateur.
    """
    complex_pattern = re.search(r'([a-z0-9]+[_-][a-z0-9_-]+)', query_text, re.IGNORECASE)
    if complex_pattern:
        return complex_pattern.group(1).upper()
        
    pe_pattern = re.search(r'\b(pe\d+)\b', query_text, re.IGNORECASE)
    if pe_pattern:
        return pe_pattern.group(1).upper()
        
    words = query_text.upper().split()
    exclude = ["SLOT", "CARTE", "PANNE", "BOARD", "CARD", "SPARE", "REMPLACEMENT", "POUR", "SUR", "RESEAU", "PORTS", "OF", "ON"]
    for word in words:
        clean_word = re.sub(r'[^\w-]', '', word)
        if any(char.isdigit() for char in clean_word) and clean_word not in exclude:
            return clean_word
            
    return None

# ─────────────────────────────────────────────────────────────
#  2. NOUVEAU : Fuzzy Matching Engine
# ─────────────────────────────────────────────────────────────
def resolve_fuzzy_node(raw_name, model_class):
    """
    Prend le nom tapé avec des fautes de frappe et trouve le vrai nom en BDD.
    """
    if not raw_name:
        return None
        
    all_names = list(model_class.objects.values_list('ne_name', flat=True).distinct())
    raw_upper = raw_name.upper()
    
    # Étape 1 : Test direct classique (très rapide)
    for name in all_names:
        if name and raw_upper in name.upper():
            return name
            
    # Étape 2 : Fuzzy Matching (Correction des fautes de frappe)
    best_match = raw_name
    highest_ratio = 0.0
    
    for name in all_names:
        if not name: continue
        
        # On compare avec le nom complet
        ratio_full = difflib.SequenceMatcher(None, raw_upper, name.upper()).ratio()
        
        # On compare avec les sous-parties du nom (ex: "1001-TUN0005" extrait de "1001-TUN0005 (To NAB0080)")
        clean_parts = re.split(r'[\s()]+', name.upper())
        ratio_part = 0.0
        if clean_parts:
            ratio_part = max([difflib.SequenceMatcher(None, raw_upper, part).ratio() for part in clean_parts if part])
        
        best_local_ratio = max(ratio_full, ratio_part)
        
        if best_local_ratio > highest_ratio:
            highest_ratio = best_local_ratio
            best_match = name
            
    # Si le score de similarité est d'au moins 60%, on applique la correction !
    if highest_ratio >= 0.6:
        return best_match
        
    return raw_name # Fallback au nom brut si aucune correspondance proche

# ─────────────────────────────────────────────────────────────
#  Capability 1: Native Predictive SFP Analysis Engine
# ─────────────────────────────────────────────────────────────
def analyze_sfp_health_local(lang='en'):
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
            
    if lang == 'fr':
        output = ["### <i class='fas fa-search' style='color: #ff9800;'></i> Analyse Prédictive Native des Défaillances SFP\n"]
        if not critical_cases and not warning_cases:
            output.append("<i class='fas fa-check-circle' style='color: #4caf50;'></i> Tous les niveaux de puissance optique sont normaux.")
            return "\n".join(output)
            
        if critical_cases:
            output.append("<i class='fas fa-times-circle' style='color: #f44336;'></i> **DÉGRADATION CRITIQUE DÉTECTÉE (<= -38 dBm) :**")
            for s in critical_cases[:10]:
                output.append(f"- Nœud `{s.ne_name}` | Port `{s.port_name}` | Rx : **{s.rx_power} dBm**\n  *Recommandation :* Remplacement immédiat du SFP requis. Inspecter la continuité de la fibre.")
        if warning_cases:
            output.append("\n<i class='fas fa-exclamation-triangle' style='color: #ff9800;'></i> **ATTENTION - FORTE ATTÉNUATION OPTIQUE (-30 dBm à -37 dBm) :**")
            for s in warning_cases[:10]:
                output.append(f"- Nœud `{s.ne_name}` | Port `{s.port_name}` | Rx : **{s.rx_power} dBm**\n  *Recommandation :* Pertes d'insertion élevées. Planifier un nettoyage des connecteurs.")
    else:
        output = ["### <i class='fas fa-search' style='color: #ff9800;'></i> Native Predictive SFP Failure Analysis\n"]
        if not critical_cases and not warning_cases:
            output.append("<i class='fas fa-check-circle' style='color: #4caf50;'></i> All SFP optical power levels are within operational baselines.")
            return "\n".join(output)
            
        if critical_cases:
            output.append("<i class='fas fa-times-circle' style='color: #f44336;'></i> **CRITICAL DEGRADATION DETECTED (<= -38 dBm):**")
            for s in critical_cases[:10]:
                output.append(f"- Node `{s.ne_name}` | Port `{s.port_name}` | Rx: **{s.rx_power} dBm**\n  *Recommendation:* Immediate SFP replacement required. Inspect fiber continuity.")
        if warning_cases:
            output.append("\n<i class='fas fa-exclamation-triangle' style='color: #ff9800;'></i> **WARNING - HIGH OPTICAL ATTENUATION (-30 dBm to -37 dBm):**")
            for s in warning_cases[:10]:
                output.append(f"- Node `{s.ne_name}` | Port `{s.port_name}` | Rx: **{s.rx_power} dBm**\n  *Recommendation:* High insertion loss detected. Schedule a window to clean connectors.")
                
    return "\n".join(output)

# ─────────────────────────────────────────────────────────────
#  Capability 2: Native Spare Parts Reallocation Algorithm
# ─────────────────────────────────────────────────────────────
def find_spare_parts_local(faulty_node, faulty_slot, lang='en'):
    if not faulty_node:
        if lang == 'fr':
            return "<i class='fas fa-exclamation-circle' style='color: #ff9800;'></i> Nom d'équipement non reconnu. Exemple : *Panne carte slot 3 sur ARI_0010*"
        return "<i class='fas fa-exclamation-circle' style='color: #ff9800;'></i> Router hostname not recognized. Example: *Card failure on slot 3 of ARI_0010*"

    failed_card = Card.objects.filter(ne_name__icontains=faulty_node, slot_id=faulty_slot).first()
    
    if not failed_card:
        node_exists = Card.objects.filter(ne_name__icontains=faulty_node).exists()
        if node_exists:
            if lang == 'fr':
                return f"<i class='fas fa-times-circle' style='color: #f44336;'></i> Aucune carte matérielle enregistrée sur le **slot {faulty_slot}** pour `{faulty_node}`."
            return f"<i class='fas fa-times-circle' style='color: #f44336;'></i> No hardware card found registered on **slot {faulty_slot}** for `{faulty_node}`."
        if lang == 'fr':
            return f"<i class='fas fa-times-circle' style='color: #f44336;'></i> L'équipement cible `{faulty_node}` est introuvable dans l'inventaire."
        return f"<i class='fas fa-times-circle' style='color: #f44336;'></i> Target node `{faulty_node}` could not be found in hardware inventory."

    board_type = failed_card.board_type
    matching_cards = Card.objects.filter(board_type=board_type).exclude(ne_name__icontains=faulty_node)
    
    if lang == 'fr':
        output = [
            f"### <i class='fas fa-boxes' style='color: #2196f3;'></i> Gestion Intelligente des Pièces de Rechange",
            f"- **Carte défectueuse :** `{board_type}` ({failed_card.description}) sur `{failed_card.ne_name}` (Slot {faulty_slot})\n"
        ]
        if matching_cards.exists():
            output.append(f"<i class='fas fa-network-wired' style='color: #00bcd4;'></i> **Candidats de réallocation trouvés ({matching_cards.count()} unités) :**")
            for card in matching_cards[:5]:
                output.append(f"  * Nœud `{card.ne_name}` | Slot `{card.slot_id}`")
            output.append(f"\n<i class='fas fa-lightbulb' style='color: #ffeb3b;'></i> **Recommandation :** Envisager une réallocation temporaire depuis un chemin non-critique listé ci-dessus.")
        else:
            output.append(f"<i class='fas fa-exclamation-triangle' style='color: #ff9800;'></i> Aucun module de rechange identique disponible sur le réseau actif.\n"
                          f"<i class='fas fa-lightbulb' style='color: #ffeb3b;'></i> **Recommandation :** Ouvrir immédiatement une demande d'approvisionnement auprès du stock central.")
    else:
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
        
        # ── LANGUAGE DETECTION STEP ──
        french_markers = ['sur', 'pour', 'panne', 'carte', 'remplacement', 'état', 'combien', 'résumé', 'réseau', 'planifier', 'dois']
        lang = 'fr' if any(marker in user_query_lower for marker in french_markers) else 'en'
        
        # 1. SFP & Optical Diagnostics (Ajout de spf, fiber)
        if re.search(r'(sfp|spf|optics|optical|rx_power|laser|fibre|fiber|optique|rx)', user_query_lower):
            response_text = analyze_sfp_health_local(lang=lang)
            
        # 2. Hardware Slot / Card Failures (Ajout de pane)
        elif re.search(r'(board|slot|card|carte|spare|panne|pane|remplacement|failure)', user_query_lower):
            slot_match = re.search(r'slot\s*(\d+)', user_query_lower)
            slot = slot_match.group(1) if slot_match else "3"
            
            raw_node = extract_node_name(user_query)
            # APPLICATION DU FUZZY MATCHING AVANT RECHERCHE
            resolved_node = resolve_fuzzy_node(raw_node, Card)
            
            response_text = find_spare_parts_local(resolved_node, slot, lang=lang)
            
        # 3. Dynamic Node Port Checking (Ajout de por, ports)
        elif re.search(r'(port|por\b|ports|interface|prt)', user_query_lower):
            raw_node = extract_node_name(user_query)
            # APPLICATION DU FUZZY MATCHING AVANT RECHERCHE
            node_name = resolve_fuzzy_node(raw_node, Port)
            
            if not node_name:
                if lang == 'fr':
                    response_text = "<i class='fas fa-exclamation-circle' style='color: #ff9800;'></i> Impossible d'extraire le nom de l'équipement. Exemple : *Ports down sur 1001-TUN0005*"
                else:
                    response_text = "<i class='fas fa-exclamation-circle' style='color: #ff9800;'></i> Unable to extract target hostname. Format sample: *Ports down on 1001-TUN0005*"
            else:
                count_down = Port.objects.filter(ne_name__icontains=node_name, oper_status__iexact="down").count()
                count_up = Port.objects.filter(ne_name__icontains=node_name, oper_status__iexact="up").count()
                total = count_down + count_up
                
                if total == 0:
                    all_distinct_nodes = list(Port.objects.values_list('ne_name', flat=True).distinct()[:3])
                    if lang == 'fr':
                        response_text = (
                            f"<i class='fas fa-info-circle' style='color: #00e5ff;'></i> Aucun port trouvé pour la chaîne `{node_name}`.\n\n"
                            f"**Note de débogage :** Nœuds existants en base : `{all_distinct_nodes}`"
                        )
                    else:
                        response_text = (
                            f"<i class='fas fa-info-circle' style='color: #00e5ff;'></i> No active interface ports matched for query string `{node_name}`.\n\n"
                            f"**Debugging note:** Current inventory contains patterns like: `{all_distinct_nodes}`"
                        )
                else:
                    if lang == 'fr':
                        response_text = (
                            f"### <i class='fas fa-chart-bar' style='color: #e040fb;'></i> Résumé de l'état des ports pour `{node_name}`\n"
                            f"- <i class='fas fa-circle' style='color: #f44336; font-size: 10px; vertical-align: middle; margin-right: 6px;'></i> Statut Opérationnel **DOWN** : **{count_down}**\n"
                            f"- <i class='fas fa-circle' style='color: #4caf50; font-size: 10px; vertical-align: middle; margin-right: 6px;'></i> Statut Opérationnel **UP** : **{count_up}**\n"
                            f"- <i class='fas fa-calculator' style='color: #9e9e9e; margin-right: 6px;'></i> Total des ports suivis : **{total}**"
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
            if lang == 'fr':
                response_text = (
                    f"### <i class='fas fa-tachometer-alt' style='color: #00e5ff;'></i> État Global du Réseau de Transmission\n"
                    f"- Total des interfaces physiques **DOWN** : **{down_ports}**\n"
                    f"- Liens Backhaul avec **Alarmes Critiques** : **{crit_links}**"
                )
            else:
                response_text = (
                    f"### <i class='fas fa-tachometer-alt' style='color: #00e5ff;'></i> Global Core Network Status\n"
                    f"- Total Interfaces Marked **DOWN**: **{down_ports}**\n"
                    f"- Operational Backhaul Links with **Critical Alarms**: **{crit_links}**"
                )
        else:
            if lang == 'fr':
                response_text = (
                    "👋 Salut! Iris est ici pour vous aidez. Posez une question sur l'infrastructure :\n\n"
                    "1. **Résumé global :** (ex: 'état du réseau')\n"
                    "2. **Diagnostics Optiques :** (ex: 'vérifier la santé des sfp')\n"
                    "3. **Gestion des pièces :** (ex: 'panne carte slot 10 sur 1001-TUN0005')\n"
                    "4. **Statistiques de ports :** (ex: 'statut des ports sur PE1')"
                )
            else:
                response_text = (
                    "👋 Welcome! Iris Here to help. Please supply a targeted infrastructure query:\n\n"
                    "1. **Core Status Summary:** (e.g., 'network health statement')\n"
                    "2. **Optical Metrics Diagnostics:** (e.g., 'check sfp lasers')\n"
                    "3. **Spare Hardware Relocations:** (e.g., 'failed board on slot 10 of node 1001-TUN0005')\n"
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
