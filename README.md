# Distribuidor de Abundância

🇧🇷 Português · [🇺🇸 English](README.en.md)

Um agente de WhatsApp que conecta quem tem comida sobrando com quem está precisando, em
Várzea Paulista e Jundiaí (SP). É um projeto social, sem fins lucrativos.

## Como funciona

Quem manda mensagem pro número escolhe uma de três portas:

| Porta | Quem é | O que a gente pergunta |
|---|---|---|
| **Quero ajudar** | Doador | O que, quanto, validade, bairro, se retira ou entrega |
| **Preciso de ajuda** | Pessoa ou família | Bairro, quantas pessoas, se tem crianças, urgência, primeiro nome |
| **Sei de alguém** | Quem indica | Os mesmos dados da família, e a confirmação de que **a família concordou** |

Os **anjos** são voluntários do bairro: validam cada novo pedido antes de ele entrar no
matching e confirmam cada entrega.

## Princípios de arquitetura

- **O LLM cuida da linguagem e o código cuida das regras.** O fluxo da conversa é uma
  máquina de estados no código. O LLM só transforma texto livre em campos estruturados.
  Veja a [ADR 0001](docs/adr/0001-llm-para-linguagem-sql-para-regras.md).
- **Matching determinístico e explicável.** Quem recebe o quê é decidido por SQL com PostGIS
  (distância, urgência, crianças, validade), nunca pelo LLM. Cada match guarda os motivos.
- **Humano no circuito.** Os anjos aprovam pedidos e confirmam entregas. O banco recusa
  pedido aprovado ou entrega confirmada sem um anjo registrado (veja o [Status](#status)).
- **Privacidade (LGPD).** Nome, telefone, endereço e localização exata ficam numa tabela
  separada, feita pra guardar esses dados criptografados pelo app. O LLM só recebe dados
  mascarados ("família de 4, bairro X"). As tabelas de doação e de pedido guardam só uma
  localização aproximada (~100 m). O doador nunca vê o nome ou o endereço de quem recebe.
- **Botões e localização em vez de texto livre.** Assim a conversa tem menos chamadas de
  LLM e menos erros.
- **Fluxos curtos.** O WhatsApp dá 1.000 mensagens de serviço grátis por número por mês.
  Depois disso, cobra.

## Stack

- Python 3.12 + FastAPI (webhook da WhatsApp Cloud API)
- Postgres + PostGIS (Supabase no MVP)
- Deploy: Azure Container Apps (escala a zero)
- Observabilidade: Langfuse + eval set de conversas fixas
- Analytics: pipeline medalhão (bronze/silver/gold) alimentando um painel público de impacto

## Estrutura

```
app/              aplicação FastAPI (app/main.py = webhook)
db/migrations/    schema SQL (Postgres + PostGIS)
db/tests/         testes SQL do schema
docs/adr/         registros de decisões de arquitetura
```

## Rodando localmente

Pré-requisitos: Python 3.12, um Postgres com PostGIS (Docker ou Supabase) e um app na Meta
com o produto WhatsApp.

**bash** (Linux, macOS, Git Bash)

```bash
# 1. Ambiente e dependências
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"

# 2. Variáveis de ambiente
cp .env.example .env             # preencha os valores WA_*

# 3. Banco (opcional por enquanto: o webhook ainda não usa o banco)
docker run -d --name abundancia-db -e POSTGRES_PASSWORD=postgres \
  -e POSTGRES_DB=abundancia -p 5432:5432 postgis/postgis:16-3.4
psql "postgresql://postgres:postgres@localhost:5432/abundancia" \
  -f db/migrations/001_initial.sql

# 4. Servidor
uvicorn app.main:app --reload --port 8000 --env-file .env

# 5. HTTPS público pra Meta alcançar o webhook
cloudflared tunnel --url http://localhost:8000   # ou: ngrok http 8000
```

**PowerShell** (Windows)

```powershell
# 1. Ambiente e dependências
python -m venv .venv
.venv\Scripts\Activate.ps1       # se bloquear: Set-ExecutionPolicy -Scope CurrentUser RemoteSigned
pip install -e ".[dev]"

# 2. Variáveis de ambiente
Copy-Item .env.example .env      # preencha os valores WA_*

# 3. Banco (opcional por enquanto: o webhook ainda não usa o banco)
docker run -d --name abundancia-db -e POSTGRES_PASSWORD=postgres `
  -e POSTGRES_DB=abundancia -p 5432:5432 postgis/postgis:16-3.4
Get-Content db\migrations\001_initial.sql | docker exec -i abundancia-db `
  psql -U postgres -d abundancia -v ON_ERROR_STOP=1

# 4. Servidor
uvicorn app.main:app --reload --port 8000 --env-file .env

# 5. HTTPS público pra Meta alcançar o webhook
cloudflared tunnel --url http://localhost:8000   # ou: ngrok http 8000
```

No PowerShell, a migration roda pelo `psql` de dentro do container, então você não precisa
instalar o cliente do Postgres no Windows. O `--env-file` do uvicorn usa o `python-dotenv`,
que já vem com o `uvicorn[standard]`.

### Testes do schema

O [`db/tests/001_schema_test.sql`](db/tests/001_schema_test.sql) confere as regras que o banco
impõe: categorias válidas, aprovação pelo anjo, consentimento da família, a trigger de
`matches` e mais. Ele roda numa transação que termina em `ROLLBACK`, então não deixa dados
no banco. Na primeira regra quebrada, o psql para com código de saída diferente de zero.
Rode depois de aplicar a migration:

```bash
psql "postgresql://postgres:postgres@localhost:5432/abundancia" \
  -v ON_ERROR_STOP=1 -f db/tests/001_schema_test.sql
```

```powershell
Get-Content db\tests\001_schema_test.sql | docker exec -i abundancia-db `
  psql -U postgres -d abundancia -v ON_ERROR_STOP=1
```

Cada regra aparece como uma linha `PASS`, e no fim vem `All schema tests passed.`

### Webhook na Meta

Por último, no painel da Meta (WhatsApp > Configuration), cadastre a URL
`https://<seu-tunel>/webhook` com o mesmo `WA_VERIFY_TOKEN` e assine o campo `messages`.

## Status

MVP em construção.

**Já existe**
- Webhook: valida a assinatura da Meta, deduplica mensagens (em memória) e mostra o menu das
  três portas.
- Schema do banco ([`001_initial.sql`](db/migrations/001_initial.sql)): o webhook ainda não
  usa esse schema. O banco já impõe:
  - pedido aprovado, rejeitado ou atendido precisa ter um anjo e uma data de validação;
  - só pedido aprovado pode receber match (trigger em `matches`);
  - entrega confirmada precisa ter um anjo e uma data de confirmação;
  - indicação só é gravada com o consentimento da família;
  - número de crianças não pode passar do tamanho da família.

**Planejado**
- **Criptografia da PII:** a tabela `person_pii` só tem colunas `bytea` pra guardar dados
  cifrados. Quem vai cifrar é o app, com chave vinda de variável de ambiente, e isso ainda
  não foi implementado. O banco não criptografa nada sozinho.
- **Regras dos anjos:** o banco ainda não confere se o anjo está ativo nem se ele é do mesmo
  bairro do pedido.
- Persistir o estado da conversa e a deduplicação no Postgres, extrair campos com o LLM,
  fazer o matching em SQL e montar o fluxo dos anjos no WhatsApp.

As decisões de arquitetura ficam em [`docs/adr/`](docs/adr/).

## Licença

[MIT](LICENSE) © David Nascimento da Silva
