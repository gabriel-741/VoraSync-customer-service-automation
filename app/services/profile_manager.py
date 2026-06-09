import json

ALLOWED_FIELDS = {
    "nome",
    "empresa",
    "segmento",
    "cargo",
    "cidade",
    "orcamento",
    "interesse",
    "necessidades",
    "objecoes",
    "etapa_venda",
    "tamanho_empresa",
    "decisor",
    "prazo_decisao",
    "produto_atual",
    "processo_atual",
    "urgencia",
    "resumo_cliente",
}

MAX_LIST_ITEMS = 6
MAX_PROFILE_SIZE = 3000


def normalize_profile(profile: dict) -> dict:

    cleaned = {}

    for key, value in profile.items():

        if key not in ALLOWED_FIELDS:
            continue

        if isinstance(value, list):

            unique = []

            for item in value:
                if item not in unique:
                    unique.append(item)

            value = unique[-MAX_LIST_ITEMS:]

        cleaned[key] = value

    profile_size = len(
        json.dumps(
            cleaned,
            ensure_ascii=False
        )
    )

    if profile_size > MAX_PROFILE_SIZE:

        for field in ["necessidades", "objecoes"]:

            if field in cleaned:
                cleaned[field] = cleaned[field][-3:]

    return cleaned