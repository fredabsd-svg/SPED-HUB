# Continuidade — este arquivo foi dividido

Este documento mantinha ao mesmo tempo o estado das fases, o histórico de
mudanças, as pendências e os próximos passos. Quatro finalidades num arquivo
só significavam quatro chances de divergir do código — e ele já divergia:
trazia uma contagem fixa de testes, que envelhece a cada commit, e marcava
fase concluída sem apontar o teste que provava.

O conteúdo continua no repositório, cada parte em um lugar com dono:

| O que você procura | Onde está |
|---|---|
| Estado real de cada fase, com o teste que prova | [`docs/status.md`](docs/status.md) |
| O que mudou em cada versão | [`CHANGELOG.md`](CHANGELOG.md) |
| O que ainda **não** existe | [`docs/roadmap.md`](docs/roadmap.md) |
| Por que cada decisão estrutural foi tomada | [`docs/decisions/`](docs/decisions/) |
| Como se trabalha neste repositório | [`REGRAS-DO-PROJETO.md`](REGRAS-DO-PROJETO.md) |
| O que cada módulo faz e não faz | [`docs/modules/`](docs/modules/) |

Comece por [`REGRAS-DO-PROJETO.md`](REGRAS-DO-PROJETO.md) e depois
[`docs/status.md`](docs/status.md).
