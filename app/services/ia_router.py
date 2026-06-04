# app/services/ai_router.py

TASK_MODELS = {
    "simple":  "gpt-4o-mini",    # respostas rápidas, FAQ
    "complex": "gpt-4.1-mini",   # orçamentos, arquivos, decisões
}

def classify_task(message: str) -> str:
    """
    Decide a complexidade da tarefa.
    Futuramente pode usar IA para classificar.
    """
    complex_triggers = ["orçamento", "contrato", "arquivo", "documento", "pdf"]

    msg = message.lower()
    if any(t in msg for t in complex_triggers):
        return "complex"
    return "simple"