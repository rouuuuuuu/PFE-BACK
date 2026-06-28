import json
from django.core.management.base import BaseCommand
from django.conf import settings
from openai import OpenAI
from sklearn.metrics import classification_report

# Imports for your RAG architecture
from ai.views import classify_intent, build_rag_context, SYSTEM_PROMPT

class Command(BaseCommand):
    help = "Stress-test IRIS Semantic Router and calculate F1, Precision, and Recall metrics."

    def handle(self, *args, **kwargs):
        self.stdout.write(self.style.WARNING("🚀 Launching Advanced Metrics Evaluation for DRS010...\n"))

        client = OpenAI(
            base_url=getattr(settings, 'OPENROUTER_BASE_URL', "https://openrouter.ai/api/v1"),
            api_key=settings.OPENROUTER_API_KEY,
        )
        IRIS_MODEL = "meta-llama/llama-3.3-70b-instruct"

        # The Golden Dataset
        test_dataset = [
            {"query": "Est-ce qu'on a des SFPs critiques sur le réseau ?", "expected_intent": "sfp"},
            {"query": "Donne-moi l'état des ports pour le noeud Tunis-PE-01", "expected_intent": "ports"},
            {"query": "Fais-moi un résumé des alarmes critiques du réseau", "expected_intent": "network_summary"},
            {"query": "Est-ce qu'un problème de signal optique SFP peut mettre le port en statut DOWN ?", "expected_intent": "sfp"}, 
            {"query": "Affiche le statut de l'équipement Gafsa-RT-03 et dis-moi s'il a des alarmes", "expected_intent": "network_summary"},
            {"query": "On doit faire un remplacement de carte slot 2 sur Tunis-PE-01, montre-moi les ports", "expected_intent": "spare_parts"},
            {"query": "Ya de la dispo en spare pr les cartes en panne sur Sousse ?", "expected_intent": "spare_parts"},
            {"query": "Le routeur de Sfax il a quoi comme config ou model ?", "expected_intent": "router"},
            {"query": "Quelle est la météo aujourd'hui à Tunis ?", "expected_intent": "general"},
            {"query": "Écris-moi un script python pour pinguer une adresse IP", "expected_intent": "general"},
        ]

        # ─── 1. METRICS CALCULATION (CLASSIFIER) ───
        self.stdout.write(self.style.SUCCESS("=== 📊 INTENT CLASSIFICATION METRICS ==="))
        
        y_true = []
        y_pred = []

        for item in test_dataset:
            prediction = classify_intent(item["query"])
            predicted_intent = prediction["intent"]
            expected = item["expected_intent"]
            
            # Append to our lists for scikit-learn
            y_true.append(expected)
            y_pred.append(predicted_intent)
            
            status_icon = "✅" if predicted_intent == expected else "❌"
            self.stdout.write(f"Query: '{item['query']}'\n   -> Expected: {expected} | Predicted: {predicted_intent} [{status_icon}]\n")

        # Generate the professional classification report
        self.stdout.write(self.style.SUCCESS("\n=== 📈 SCIKIT-LEARN CLASSIFICATION REPORT ==="))
        # zero_division=0 prevents warnings if an intent was never predicted
        report = classification_report(y_true, y_pred, zero_division=0)
        self.stdout.write(report)


        # ─── 2. LLM JUDGE EVALUATION (RAG TRIAD) ───
        self.stdout.write(self.style.SUCCESS("\n=== 🧠 GENERATOR EVALUATION (LLM JUDGE) ==="))
        
        sample = test_dataset[3] # Testing a complex query
        user_query = sample["query"]
        self.stdout.write(f"Testing complex query: '{user_query}'")
        
        intent_data = classify_intent(user_query)
        context = build_rag_context(intent_data)
        
        self.stdout.write("1️⃣  IRIS is generating a response based on PostgreSQL data...")
        iris_prompt = f"NETWORK DATA CONTEXT:\n{context}\n\nUSER QUESTION: {user_query}"

        try:
            iris_response = client.chat.completions.create(
                model=IRIS_MODEL,
                messages=[
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": iris_prompt}
                ]
            )
            actual_iris_answer = iris_response.choices[0].message.content.strip()
            
            self.stdout.write("2️⃣  The LLM Judge is evaluating faithfulness...")
            judge_prompt = f"""You are a strict QA evaluator for a Telecom RAG system.
Evaluate the FAITHFULNESS of the AI Response compared ONLY to the provided Data Context.
Rate from 1 to 5.
CONTEXT:\n{context}\n\nAI RESPONSE:\n{actual_iris_answer}
Output your response ONLY as a raw JSON object:
{{"score": 5, "reason": "explanation"}}"""

            judge_response = client.chat.completions.create(
                model=IRIS_MODEL,
                messages=[{"role": "user", "content": judge_prompt}],
                temperature=0.0
            )
            
            result_text = judge_response.choices[0].message.content.replace("```json", "").replace("```", "").strip()
            result_json = json.loads(result_text)
            
            self.stdout.write(self.style.SUCCESS(f"\n⭐ Faithfulness Score (LLM Judge): {result_json['score']}/5"))
            self.stdout.write(f"📝 Judge Reason: {result_json['reason']}")
            
        except Exception as e:
            self.stdout.write(self.style.ERROR(f"❌ Error during evaluation: {e}"))
