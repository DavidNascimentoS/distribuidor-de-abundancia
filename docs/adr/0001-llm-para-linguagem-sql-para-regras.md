# ADR 0001 — LLM para linguagem, código e SQL para regras

- **Status:** Aceita
- **Data:** 2026-10-11

## Contexto

O Distribuidor de Abundância conversa pelo WhatsApp com três públicos: quem quer doar, quem
precisa de ajuda e quem indica uma família. As pessoas escrevem do jeito que falam ("tenho
umas 3 cestas, vencem mês que vem, moro perto da praça"), então precisamos de um LLM pra
entender texto livre.

Mas as decisões que importam são sensíveis: quem recebe qual doação, em que ordem, e quem vê
quais dados. Elas precisam ser:

- **justas e explicáveis:** dá pra responder "por que essa família e não aquela?";
- **reproduzíveis:** a mesma entrada gera a mesma saída, então dá pra testar;
- **auditáveis:** os anjos e o projeto conseguem revisar;
- **baratas e previsíveis:** projeto sem fins lucrativos, com cota limitada de mensagens;
- **seguras para a LGPD:** dados pessoais não podem ir para um provedor de LLM.

Um LLM conduzindo a conversa e escolhendo os matches não atende nenhum desses requisitos de
forma confiável.

## Decisão

1. **O fluxo da conversa é uma máquina de estados no código.** Cada porta tem passos fixos
   (`conversations.porta`, `conversations.step`). O código decide qual é a próxima pergunta.
   Sempre que possível, a resposta vem por botão ou localização nativa do WhatsApp, sem LLM.
2. **O LLM só extrai campos.** Quando a resposta é texto livre, o LLM recebe o texto e
   devolve uma saída estruturada (ex.: `{categoria, quantidade, unidade, validade}`), que é
   validada com Pydantic. Se a validação falha, a pergunta é refeita. O LLM não decide o
   próximo passo nem fala nada que não foi pedido.
3. **O LLM só vê dados mascarados.** Nome, telefone e endereço ficam em `person_pii`,
   criptografados pelo app, e nunca entram em prompt. O contexto vai como "família de 4, bairro X".
4. **O matching é SQL determinístico.** Distância (PostGIS), urgência, presença de crianças
   e validade da doação entram numa query com pesos explícitos. Cada match grava `reasons`
   (jsonb) com os fatores que levaram a ele.
5. **Humanos fecham o ciclo.** Um anjo do bairro aprova cada novo pedido e confirma cada
   entrega. O schema usa CHECKs pra recusar aprovação ou confirmação sem um anjo registrado.

## Consequências

**Positivas**
- Todo match pode ser explicado para a família, para o doador e para os anjos.
- As regras são testáveis com testes comuns. O LLM é avaliado separadamente, com um eval set
  de conversas fixas (Langfuse).
- Menos chamadas de LLM: botões e localização dispensam extração, o que reduz custo e
  latência.
- Trocar de modelo ou de provedor afeta só a extração, não as regras.
- Fica fácil cumprir a LGPD: nenhum dado pessoal sai para o provedor de LLM.

**Negativas**
- A conversa é menos "natural": quem foge muito do roteiro recebe a pergunta de novo, em vez
  de uma resposta improvisada.
- Cada novo campo ou passo exige mudar código e estado, não só um prompt.
- Os pesos do matching são escolhidos à mão e precisam de revisão com dados reais.

## Alternativas consideradas

- **LLM como agente livre conduzindo a conversa (tool calling).** É mais flexível, mas o
  comportamento não é determinístico, o custo por conversa é maior e fica difícil garantir que
  nenhum dado pessoal entre no contexto. Rejeitada.
- **LLM escolhendo o match** (receber a lista de pedidos e "decidir quem precisa mais").
  Não é explicável nem reproduzível, pode trazer vieses difíceis de detectar e exige mandar
  dados sensíveis ao modelo. Rejeitada.
- **Sem LLM, só botões e listas.** É o mais barato e previsível, mas trava em campos como
  "o que você tem pra doar". Adotamos isso como o padrão e usamos o LLM só onde texto livre é
  inevitável.
