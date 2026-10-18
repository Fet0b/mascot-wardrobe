# Arquitetura e limites

Este projeto é uma cópia de desenvolvimento: não instala serviços, não altera configurações externas e não publica fotos em contas reais.

## Linguagem do domínio

- **Mascote**: identidade, PNG de origem e caixa corporal normalizada. Não há reconhecimento automático do rosto.
- **Perfil do país**: cinco limites crescentes que definem seis categorias, paleta e descrição das peças.
- **Guarda-roupa**: seis conjuntos preparados antes da seleção por clima.
- **Categoria**: muito frio, frio, ameno, ambiente, calor e muito calor. O pedido enumerava cinco nomes para seis faixas; `ameno` é a sexta categoria adotada.
- **Seleção**: escolhe um conjunto existente pela temperatura, sem gerar imagens a cada consulta.
- **Canal**: aplicação/verificação local do conjunto ou exportação de arquivos, não uma promessa de publicação remota.

## Organização DDD pragmática

- `domain/`: objetos de valor e regras puras; não conhece rede, Pillow ou armazenamento.
- `application/`: casos de uso de seleção e coordenação entre os contextos.
- `wardrobe/`: contexto delimitado de preparação das roupas; `WardrobeAgent` coordena um `Renderer` substituível. O compositor padrão usa Pillow localmente.
- `channels/`: contexto de distribuição; inclui bridge HTTP sandbox compatível com a lógica de atualização/leitura do legado e exportadores de arquivos.
- `infrastructure/`: repositório JSON de perfis e adaptador meteorológico.
- `interfaces/`: entrada de linha de comando.
- `legacy/`: cópia de referência do programa anterior; não é importada pelo novo pacote. Seus scripts não são o ponto de entrada recomendado e não devem ser executados contra produção.

É uma separação por contextos e responsabilidades, não uma alegação de arquitetura enterprise completa. O contexto `wardrobe` reúne caso de uso e adaptador gráfico no mesmo módulo; a porta `Renderer` permite separar ou substituir o compositor sem alterar as regras de temperatura.

## Perfis ajustáveis

Os perfis de BR, GB, US e JP são presets de produto, não padrões oficiais, recomendações de saúde nem descrições de todo o clima de um país. A pessoa informa seu país explicitamente; não há rastreamento, geolocalização ou inferência de nacionalidade. Um país pode exigir perfis regionais futuros.

BR, em °C, com o limite pertencendo à faixa superior:

- Muito frio: abaixo de 5.
- Frio: de 5 até menos de 15.
- Ameno: de 15 até menos de 20.
- Ambiente: de 20 até menos de 25.
- Calor: de 25 até menos de 32.
- Muito calor: a partir de 32.

## O que o agente de roupas faz hoje

Recebe PNG e região corporal, prepara seis composições geométricas locais e exporta retratos e atlas. Não usa um modelo generativo, não interpreta semanticamente anatomias e não cria roupas realistas. As descrições de peças ficam disponíveis como briefs para um futuro provedor injetável, mas o compositor atual segue moldes fixos.

A cara fora da caixa corporal é preservada pixel a pixel. Atlas usam deslocamentos geométricos, não animação articulada. Não há cobrança de inferência nem chamadas de LLM na execução deste pacote.

## Integração sem implantação

O contrato do bridge legado é exercitado contra HTTP loopback efêmero e arquivos separados. A leitura posterior verifica bytes no sandbox; não comprova publicação no WhatsApp real, que pode recomprimir imagens. Telegram, Discord e `hermes-cli` recebem exportações locais, não alterações de contas ou instalação automática de mascotes.

Uma publicação real futura exige autorização de destino, credenciais fora do repositório, adaptação por provedor e leitura posterior. Não habilitar o código legado como atalho para esse fluxo.

## Segurança operacional

A sandbox é uma barreira lógica, não isolamento do sistema operacional. Execute em pastas dedicadas sem outros escritores privilegiados. Não copiar `.env`, autenticação do bridge, logs, sessões ou estado pessoal para GitHub. `audit/`, `sandbox/` e arquivos de ambiente ficam ignorados. O inventário local em `audit/production-source-baseline.json` permite conferir que a cópia não modificou os arquivos conhecidos de produção; arquivos operacionais podem mudar independentemente por tarefas já ativas.

O exemplo visual foi convertido do SVG preexistente. A presença do arquivo não concede direitos sobre marcas ou personagens de terceiros; substitua por um mascote próprio e revise as licenças antes de publicar.
