# app/services/classifier.py

COMPLEX_TRIGGERS = ["orçamento", "contrato", "arquivo", "documento", "pdf", "relatório"]

def classify_message(message: str) -> dict:
    """
    Só decide complexidade — nada mais.
    A decisão de extrair perfil fica com a IA.
    """
    msg = message.lower().strip()

    return {
        "model_tier": "complex" if any(t in msg for t in COMPLEX_TRIGGERS) else "simple"
    }
