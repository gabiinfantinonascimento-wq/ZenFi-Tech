# ZenFi Tech — app financeiro com nuvem, WhatsApp e planos

O ZenFi continua sendo **um servidor Python simples + um HTML** (`zenfi_whatsapp.html`), sem bibliotecas externas.
Esta versão acrescenta: contas na nuvem, sincronização entre aparelhos, lançamento por WhatsApp (API oficial da Meta),
recuperação de senha e planos (Gratuito / Pro R$ 44,90 por mês).

## O que tem na pasta
| Arquivo | Para quê |
|---|---|
| `servidor_zenfi_whatsapp.py` | Servidor: login, nuvem, webhook da Meta, planos, pagamento |
| `zenfi_parser.py` | Entende as mensagens ("Gastei R$ 35 no almoço") |
| `zenfi_whatsapp.html` | O aplicativo (mesmo visual de antes + sincronização e telas novas) |
| `manifest.webmanifest`, `sw.js`, `icon-*.png` | App instalável (PWA) com ícone e uso offline |
| `android-app/`, `.github/workflows/android.yml` | App Android (.apk) com microfone; veja `android-app/LEIA-ME-ANDROID.md` |
| `Procfile`, `requirements.txt`, `render.yaml` | Publicação no Render |
| `.env.exemplo` | Lista de todas as variáveis (sem valores) |
| `tests/` | Testes automáticos |

## Como funciona (resumo)
- **Cadastro e login:** nome, e-mail e senha (CPF/CNPJ é opcional). Senha guardada com PBKDF2‑SHA256 (200 mil voltas + sal). Sessão em cookie `HttpOnly`, `SameSite=Lax` e `Secure` quando em HTTPS; 5 senhas erradas = pausa de 10 min.
- **Nuvem:** tudo que o app guarda (receitas, despesas, categorias, contas, metas, perfil, configurações) vai para o banco no servidor, separado por usuário. O `localStorage` continua sendo usado como cópia local rápida/offline e é reenviado quando a internet volta. Ao entrar em outro celular os dados vêm sozinhos. Itens de PIN/bloqueio e preferências de tela deste aparelho **não** sobem.
- **Dois aparelhos ao mesmo tempo:** se você lança algo no celular e outra coisa no computador, o servidor **junta** os dois (não perde lançamento). Os aparelhos abertos consultam novidades a cada 6 s; quando chega algo novo (inclusive do WhatsApp) a tela se atualiza sozinha assim que você não estiver digitando.
- **WhatsApp:** em *Configurações → Lançar pelo WhatsApp* o usuário gera um código `ZEN-123456` e o envia **do próprio WhatsApp** para o número oficial. O servidor lê o número que a Meta informa (`from`), vincula à conta e passa a identificar o usuário por ele. Depois, mensagens como *Gastei R$ 35 no almoço*, *Paguei R$ 120 de gasolina*, *Recebi R$ 2.500 de salário*, *Paguei R$ 90 de internet* viram lançamentos (valor, descrição, categoria, data — aceita "ontem", "dia 5", "28/10") direto na conta, e a pessoa recebe a confirmação no WhatsApp com o saldo do mês.
- **Sem duplicar:** a Meta pode reenviar o mesmo webhook → ignorado pelo ID da mensagem. Mensagem igual (mesmo valor/descrição/data) em menos de 2 minutos → não lança de novo e avisa.
- **Planos:** *Gratuito* = até 30 lançamentos por mês e sem WhatsApp. *Pro* (R$ 44,90/mês) = ilimitado + WhatsApp. Toda conta nova ganha **7 dias de Pro** (mude em `TRIAL_DIAS`; `0` desliga). O limite é aplicado no servidor.


## Assistente do WhatsApp (o que ele entende)
- **Lançamentos:** *Gastei R$ 35 no almoço*, *Recebi 2 mil de freela*, *paguei 80 dia 5 de luz*, *gastei vinte reais no pão*, *ontem gastei 50 no uber*, *na sexta paguei 200 de mecânico*.
- **Atalhos sem verbo:** *almoço 35*, *uber 18,90*, *salário 3000*, *+500 pix*, *-30 café*.
- **Vários de uma vez:** *gastei 30 no lanche e 20 no uber* (até 8 por mensagem, também em linhas separadas).
- **Comandos:** `saldo`, `resumo` (e *resumo do mês passado*), `extrato` (últimos 5 feitos por aqui), `desfazer` (apaga o último lançamento/mensagem feito por aqui, últimas 24 h; some do app também), `ajuda`, e saudações (*oi*, *bom dia*).
- **Duplicidade:** igual em 2 min não é lançado de novo; escreva *de novo* para forçar. Lançamento desfeito não conta como duplicado.
- **Erros explicados:** sem valor, sem saber se é gasto/receita, moeda estrangeira, compra parcelada (*3x de 50*) e valor alto recebem orientação específica; nada é lançado “pela metade”.
- **Robustez:** respostas saem na ordem das mensagens (fila única); reações (👍) e avisos do sistema não geram resposta; botões/listas da Meta são lidos como texto; se algo falhar internamente a pessoa recebe aviso em vez de silêncio.

## Rodar no seu computador (teste)
```bash
python servidor_zenfi_whatsapp.py        # abre em http://localhost:8000
```
Sem as credenciais da Meta/Stripe/e‑mail o app funciona (cadastro, login, nuvem), mas WhatsApp, pagamento e envio de e‑mail avisam que ainda não foram configurados — nada é simulado.

Testes automáticos:
```bash
python -m unittest discover -s tests -v          # 29 testes (parser + servidor, sem internet)
pip install playwright && playwright install chromium
python tests/e2e_browser.py                      # 16 passos em navegador: 2 "celulares", WhatsApp, senha, sair
```

## Publicar no Render (passo a passo)
1. Crie um repositório no GitHub e suba **o conteúdo desta pasta** na raiz (o `.gitignore` já impede subir banco e `.env`).
2. No Render: **New + → Blueprint** e escolha o repositório (usa o `render.yaml`) — ou **New + → Web Service** manualmente:
   Runtime *Python*, Build `pip install -r requirements.txt`, Start `python servidor_zenfi_whatsapp.py`, Health check `/healthz`.
3. **Disco persistente (obrigatório):** o banco é um arquivo SQLite. Em *Disks* crie um disco montado em `/var/data` e defina `DATA_DIR=/var/data`. Sem disco, **os dados somem a cada deploy/reinício**. (Disco exige plano pago; use **uma única instância** — não escale para várias.)
4. Em *Environment* cadastre as variáveis (tabela abaixo). **Nunca** coloque tokens no código ou no GitHub.
5. Depois do deploy, abra `https://SEU-APP.onrender.com`, crie sua conta e confira.
6. Faça backup: baixe o arquivo `/var/data/zenfi.db` periodicamente (Render Shell ou *Disk snapshots*).

### Variáveis de ambiente
| Variável | Obrigatória? | O que é |
|---|---|---|
| `DATA_DIR` | sim (produção) | Pasta do banco, `/var/data` |
| `BASE_URL` | sim | Endereço público, ex. `https://zenfi.onrender.com` (links de e‑mail e retorno do pagamento) |
| `META_VERIFY_TOKEN` | WhatsApp | Texto secreto que você inventa; igual ao campo "Verify token" da Meta |
| `META_APP_SECRET` | WhatsApp | *App Secret* do aplicativo na Meta (valida a assinatura de cada webhook) |
| `WHATSAPP_TOKEN` | WhatsApp | Token **permanente** (usuário do sistema) com permissão `whatsapp_business_messaging` |
| `WHATSAPP_PHONE_NUMBER_ID` | WhatsApp | ID do número no painel da Meta |
| `WHATSAPP_NUMERO_PUBLICO` | WhatsApp | O número que as pessoas vão chamar, só dígitos (ex. `5511999999999`) |
| `WHATSAPP_VERIFY_TEMPLATE` | opcional | Nome de template de autenticação (só se quiser verificar por código enviado pela Meta) |
| `STRIPE_SECRET_KEY`, `STRIPE_PRICE_ID`, `STRIPE_WEBHOOK_SECRET` | pagamento | Veja "Assinatura" |
| `SMTP_HOST`, `SMTP_PORT`, `SMTP_USER`, `SMTP_PASS`, `SMTP_FROM` | recuperar senha | Servidor de e‑mail que envia o link |
| `TRIAL_DIAS`, `LIMITE_GRATIS_LANCAMENTOS` | opcional | 7 e 30 por padrão |

## Configurar o WhatsApp (Meta)
1. Em developers.facebook.com crie um app do tipo **Business** e adicione o produto **WhatsApp**. Cadastre/verifique sua empresa e um número de telefone para o ZenFi (esse é o "número oficial").
2. Em *API Setup* copie o **Phone number ID** → `WHATSAPP_PHONE_NUMBER_ID`. Em *Configurações do app → Básico* copie o **App Secret** → `META_APP_SECRET`.
3. Crie um **usuário do sistema** no Business Manager, dê acesso ao app e gere um **token permanente** → `WHATSAPP_TOKEN`.
4. *WhatsApp → Configuration → Webhook*: URL de callback `https://SEU-APP.onrender.com/webhook/whatsapp`, Verify token = o valor de `META_VERIFY_TOKEN`. Clique em **Verify and save** e **assine o campo `messages`**.
5. Defina `WHATSAPP_NUMERO_PUBLICO` e reinicie. No app: Configurações → Lançar pelo WhatsApp → *Gerar código*.
6. Em modo de teste a Meta só entrega para números cadastrados como testadores; para qualquer cliente é preciso concluir a verificação do negócio e colocar o app em modo **Live**.
Observação: a resposta de confirmação é texto livre, permitido dentro de 24 h depois que **a pessoa** escreve (sempre o caso aqui). Não é preciso template.

## Configurar a assinatura (Stripe)
1. Crie conta na Stripe (Brasil), um **Produto "ZenFi Pro"** com preço **recorrente mensal de R$ 44,90** e copie o `price_...` → `STRIPE_PRICE_ID`. Chave secreta → `STRIPE_SECRET_KEY`.
2. *Developers → Webhooks → Add endpoint*: `https://SEU-APP.onrender.com/webhook/stripe`, eventos `checkout.session.completed`, `customer.subscription.created`, `customer.subscription.updated`, `customer.subscription.deleted`. Copie o *Signing secret* → `STRIPE_WEBHOOK_SECRET`.
3. Ative o **Customer Portal** (Configurações → Billing) para o botão "Gerenciar assinatura" (cancelar, trocar cartão).
Se preferir Mercado Pago/Asaas/etc., só `/api/assinatura/checkout` e `/webhook/stripe` precisam ser trocados — o controle de plano (`plano`, `plano_ate`) já está pronto.

## Recuperar senha
Configure o SMTP (Gmail com senha de app, Brevo, SendGrid, Amazon SES…). O link vale 1 hora, serve uma vez e, ao trocar a senha, todos os aparelhos saem da conta. Sem SMTP o e‑mail **não é enviado** (o servidor avisa no log).

## Limites conhecidos (seja honesto com seus clientes)
- Banco **SQLite em disco único**: ótimo para começar (centenas/poucos milhares de usuários), mas não escala para várias instâncias. Para crescer, migrar para PostgreSQL gerenciado.
- Atualização entre aparelhos por **consulta a cada 6 s** (não é push instantâneo). Quando chega dado novo a página recarrega sozinha se você não estiver digitando.
- O entendimento das mensagens é por regras (palavras‑chave). Áudio, foto e comprovante ainda não são lidos; mensagens sem valor ou sem verbo (gastei/paguei/recebi…) recebem pedido para reescrever e **não** lançam nada.
- Os testes usam servidores falsos da Meta e da Stripe (nada é cobrado nem enviado de verdade). Antes de abrir ao público teste com o número real de testes da Meta e uma compra de teste da Stripe.
- Privacidade: publique política de privacidade/termos (LGPD) e defina por quanto tempo guarda dados.

## Microfone e área automática (atualização)
- **Voz mais firme:** o reconhecimento agora pede 5 alternativas ao celular e usa a que contém um valor; mostra o texto ao vivo enquanto você fala; tenta de novo sozinho se não ouvir nada; entende "2 mil", "dois mil e quinhentos", "45 reais e 50 centavos", "30 reais e meio", "ontem" e "dia 12".
- **Área mais parecida:** a área do lançamento é escolhida pelo que você já lançou antes (mesma descrição), por palavras-chave e por semelhança de texto (aceita erro como "farmasia", "gazolina", "feirinha"). Se nada se parece, fica em "Outros". Vale no app, no assistente de voz e no WhatsApp.
- Teste da parte de voz/área no navegador: `node tests/voz_categoria_node.js`.
- Android: depois de publicar, gere o .apk de novo (a voz nativa agora devolve várias alternativas).
