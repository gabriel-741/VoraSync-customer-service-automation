#app/services/profile_manager.py

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

MAX_STRING_SIZE = 500
MAX_LIST_ITEM_SIZE = 200


def normalize_profile(profile: dict) -> dict:

    cleaned = {}

    for key, value in profile.items():

        if key not in ALLOWED_FIELDS:
            continue

        if isinstance(value, str):
            value = value.strip()[:MAX_STRING_SIZE]

        if isinstance(value, list):

            cleaned_list = []

            for item in value:

                if isinstance(item, str):
                    item = item.strip()[:MAX_LIST_ITEM_SIZE]

                if not item:
                    continue

                if item not in cleaned_list:
                    cleaned_list.append(item)

            value = cleaned_list[-MAX_LIST_ITEMS:]



        cleaned[key] = value

    profile_size = len(
        json.dumps(
            cleaned,
            ensure_ascii=False
        )
    )

    while len(json.dumps(cleaned, ensure_ascii=False)) > MAX_PROFILE_SIZE:

        for field in ["necessidades", "objecoes"]:

            if field in cleaned and len(cleaned[field]) > 1:
                cleaned[field] = cleaned[field][1:]

    return cleaned