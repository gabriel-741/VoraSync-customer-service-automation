# app/services/classifier.py

GREETING_TRIGGERS  = ["oi", "olá", "ola", "bom dia", "boa tarde", "boa noite", "hey"]
HUMAN_TRIGGERS     = ["humano", "atendente", "pessoa", "falar com alguém"]
FAQ_TRIGGERS       = ["preço", "valor", "plano", "horário", "funciona", "como"]
SIMPLE_TRIGGERS    = ["sim", "não", "ok", "obrigado", "obrigada", "certo", "entendi"]

def classify_message(message: str) -> str:
    """
    Classifica a mensagem antes de chamar a IA.
    Retorna: greeting | human | faq | simple | ai
    """
    msg = message.lower().strip()

    if any(t in msg for t in HUMAN_TRIGGERS):
        return "human"

    if any(t == msg for t in GREETING_TRIGGERS):   # match exato para saudações
        return "greeting"

    if any(t in msg for t in SIMPLE_TRIGGERS) and len(msg.split()) <= 3:
        return "simple"

    if any(t in msg for t in FAQ_TRIGGERS):
        return "faq"

    return "ai"   # só chega aqui o que realmente precisa de IA