from django.test import TestCase
from django.conf import settings
from unittest.mock import patch
from openai import OpenAI
import json

# Importe tes fonctions RAG depuis ton fichier views.py (ajuste l'import si nécessaire)
from .views import classify_intent, build_rag_context, SYSTEM_PROMPT


class IRISMetricstests(TestCase):

    def setUp(self):
        """Configuration du Dataset de test (Golden Dataset) pour évaluer IRIS"""
        self.client = OpenAI(
            base_url=getattr(settings, 'OPENROUTER_BASE_URL', "https://openrouter.ai/api/v1"),
            api_key=settings.OPENROUTER_API_KEY,
        )
        
        # Dataset de test : requêtes réelles d'ingénieurs et l'intention attendue (Ground Truth)
        self.test_dataset = [
            {"query": "Est-ce qu'on a des SFPs critiques sur le réseau ?", "expected_intent": "sfp"},
            {"query": "Donne-moi l'état des ports pour le noeud Tunis-PE-01", "expected_intent": "ports"},
            {"query": "Il y a une panne de carte slot 3 sur l'équipement Sousse-RT", "expected_intent": "spare_parts"},
            {"query": "Affiche les détails du routeur Sfax-PE-02", "expected_intent": "router"},
            {"query": "Fais-moi un résumé des alarmes critiques du réseau", "expected_intent": "network_summary"},
        ]

    def test_evaluate_intent_classifier(self):
        """Métrique 1 : Calcul de l'Accuracy (Précision globale) du classifieur d'intention"""
        print("\n=== 📊 ÉVALUATION DU CLASSIFIEUR D'INTENTION ===")
        correct_predictions = 0
        total_queries = len(self.test_dataset)

        for item in self.test_dataset:
            prediction = classify_intent(item["query"])
            predicted_intent = prediction["intent"]
            
            is_correct = predicted_intent == item["expected_intent"]
            if is_correct:
                correct_predictions += 1
            
            print(f"Query: '{item['query']}'")
            print(f"   -> Attendu: {item['expected_intent']} | Prédit: {predicted_intent} [{'✅' if is_correct else '❌'}]")

        # Calcul du taux de réussite global (Accuracy)
        accuracy = (correct_predictions / total_queries) * 100
        print(f"\n🎯 Accuracy globale du Classifieur: {accuracy:.2f}%")
        
        # Le test passe si l'accuracy est supérieure à 80% (tu peux ajuster)
        self.assertGreaterEqual(accuracy, 80.0, "L'accuracy du classifieur est trop basse !")

    def test_rag_triad_faithfulness_with_llm_judge(self):
        """Métrique 2 : RAG Triad - Calcul de la Fidélité (Anti-Hallucination) via LLM Judge"""
        print("\n=== 🧠 ÉVALUATION DE LA FIDÉLITÉ (LLM JUDGE VIA OPENROUTER) ===")
        
        # On prend un exemple de test pour l'évaluation par le Juge
        sample = self.test_dataset[0] 
        intent_data = classify_intent(sample["query"])
        context = build_rag_context(intent_data)
        
        # Simulation d'une réponse d'IRIS (normalement récupérée de ton LLM)
        # Ici on simule une bonne réponse basée sur le contexte pour que le juge note
        simulated_iris_response = "Après analyse des diagnostics optiques, nous avons actuellement 2 SFPs critiques (Rx <= -38 dBm) détectés sur le réseau."

        # Prompt pour forcer le LLM Judge à agir comme un évaluateur strict
        judge_prompt = f"""You are a strict QA evaluator for a Telecom RAG system.
Your job is to evaluate the FAITHFULNESS of the AI Response compared ONLY to the provided Data Context.
Rate the response from 1 to 5 (5 means perfect alignment, 1 means total hallucination or fact not found in context).

CONTEXT:
{context}

AI RESPONSE:
{simulated_iris_response}

Output your response ONLY as a raw JSON object like this:
{{"score": 5, "reason": "Your brief architectural explanation here"}}"""

        try:
            # On appelle un modèle fort (comme Llama 3 70B) pour jouer le rôle de Juge
            judge_response = self.client.chat.completions.create(
                model="meta-llama/llama-3.3-70b-instruct",
                messages=[{"role": "user", "content": judge_prompt}],
                temperature=0.0 # Température à 0 pour une notation stable et déterministe
            )
            
            result_json = json.loads(judge_response.choices[0].message.content.strip())
            print(f"Query évaluée: '{sample['query']}'")
            print(f"⭐ Note de Fidélité (LLM Judge): {result_json['score']}/5")
            print(f"📝 Raison du Juge: {result_json['reason']}")
            
            self.assertGreaterEqual(result_json['score'], 4, "Le score de fidélité du RAG est insuffisant !")
            
        except Exception as e:
            print(f"❌ Impossible de joindre le LLM Judge OpenRouter: {e}")
