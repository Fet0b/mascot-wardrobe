# Canais: somente não produção

Este contexto não acessa contas reais, credenciais, bots ou serviços externos.
A implementação existente foi consultada apenas para compatibilidade de contrato;
nenhum arquivo ou serviço de produção deve ser alterado.

## Bridge WhatsApp simulado

```python
from pathlib import Path
from mascot_wardrobe.channels import SandboxBridge, WhatsAppBridgeChannel

with SandboxBridge(Path("sandbox/catalog"), Path("sandbox/state")) as bridge:
    canal = WhatsAppBridgeChannel(bridge.base_url, timeout=5)
    resultado = canal.sync("sunny")
```

- `SandboxBridge(catalog_dir: Path, state_dir: Path)` é um context manager;
  expõe `.base_url` e `.port` enquanto aberto. Usa `ThreadingHTTPServer`,
  somente `127.0.0.1`, porta efêmera; encerra servidor e thread ao sair.
- O catálogo aprovado contém JPEGs `<slug>.jpg`, até 2 MiB, verificados com Pillow.
  Slug: `[a-z0-9][a-z0-9-]{0,100}`. Links para fora do catálogo são proibidos.
- Estado separado: `state_dir/current.jpg`, não o arquivo do catálogo.
  Cópia atômica e idempotente: conteúdo igual não reescreve o arquivo.
- `GET /health`: `{"status":"connected","sandbox":true}`.
- `GET /profile-picture?slug=sunny` e `POST /profile-picture` exigem
  `X-Mascot-Wardrobe-Sandbox: 1`; qualquer cabeçalho `Origin` é rejeitado.
  POST aceita **somente** `{"slug":"sunny"}`. GET também aceita somente slug.
  `target`, `jid`, caminhos, traversal e corpos excessivos são rejeitados.
- GET retorna `target:self`, `sandbox:true`, `matches`, `imageBase64`,
  `remoteSha256`, `slug` e `verification:sandbox-exact-source-bytes`.
  Sem foto atual, imagem e hash são `null` e `matches:false`.
- POST retorna `success:true`, `target:self`, `sandbox:true`, `verified:false`,
  `slug` e `sourceSha256`. Esse reconhecimento **não** verifica a alteração.

`WhatsAppBridgeChannel(base_url, timeout=5).sync(slug: str) -> dict` verifica health,
consulta GET, valida alvo/flag sandbox/tipos/hash e só então considera POST.
Depois de POST, um novo GET é obrigatório; ausência de confirmação gera
`RuntimeError`. Não há retries ocultos ou opção para habilitar produção.
Retorna `slug`, `action` (`updated` ou `unchanged`), `verified:true`, `target:self`,
`sandbox:true`, `remote_sha256` e `verification`. `.current_sha256` guarda o hash
confirmado em memória e é limpo no início de cada tentativa.

A URL deve ser HTTP com IP loopback literal e porta explícita, diferente de 3000.
DNS/`localhost`, credenciais na URL, query, fragmento e caminho base são recusados;
porta 3000 é recusada antes de qualquer rede. Proxy e redirects são desabilitados.
Health não sandbox ou leitura com alvo diferente impedem o POST.
`self` significa exclusivamente a própria conta no contrato legado; o sandbox
não tem conta. Igualdade de bytes só prova a cópia **simulada**, não uma foto
publicada pelo WhatsApp, que pode transformar imagens.

## Exportadores de arquivos

```python
from mascot_wardrobe.channels import FileExportChannel

exportador = FileExportChannel(Path("sandbox/output"), "telegram")
resultado = exportador.export(
    "sunny",
    Path("assets/sunny.jpg"),
    Path("assets/atlas.png"),
    Path("assets/manifest.json"),
)
```

API: `FileExportChannel(sandbox_dir: Path, channel: str)` e
`.export(slug: str, portrait: Path, atlas: Path, manifest: Path) -> dict`.
Canais: `whatsapp`, `telegram`, `discord`, `hermes-cli`.
Copia bytes sem transformação para `<sandbox_dir>/<channel>/<slug>/<nome-original>`;
os três nomes precisam ser distintos. Preserva também o manifesto original.
Valida entradas/destinos antes de escrever; recusa saída simbólica e arquivos
fora da raiz declarada. Escrita atômica por arquivo, manifesto por último;
não oferece transação atômica do conjunto. Repetição igual não reescreve.
Retorna `slug`, `channel`, `sandbox:true`, `action:exported|unchanged`,
`remote_sync:false`, `verified:false` e `files` (mapa portrait/atlas/manifest).
Não faz nenhuma chamada de bot nem afirma sincronização real.

## Limite de segurança e extensão planejada

Catálogo e estado não podem se sobrepor.
A sandbox é uma fronteira lógica de aplicação, **não isolamento do sistema
operacional contra root**. Outro processo privilegiado pode trocar diretórios,
arquivos ou falsificar um servidor com `sandbox:true`; essa flag não autentica
um servidor hostil. Execute em diretórios dedicados, sem escritores concorrentes.

Telegram, Discord e integração real do canal `hermes-cli` são **apenas planejados**.
Uma futura extensão deverá separar `prepare(slug) -> artefatos`,
`apply(artefatos) -> reconhecimento` e `readback() -> evidência`, com política
explícita de destino/autorização. Reconhecimento nunca substituirá leitura
posterior. Essa interface não está implementada; não existem tokens ou bypasses.

## Testes locais

```sh
PYTHONPATH=src python -m unittest discover -s tests -p test_channels.py -v
```

Os testes usam HTTP real somente em servidores locais efêmeros e arquivos
temporários; incluem idempotência, readback e recusas antes de efeitos.
