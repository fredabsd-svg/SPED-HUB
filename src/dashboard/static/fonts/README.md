# Fontes do dashboard web — "Tinta & Latão"

Subconjunto das fontes dos relatórios (`src/reports/templates/fonts/`,
licenças OFL lá): Source Serif 4 Semibold para títulos, Source Sans 3
Regular/Semibold para corpo e interface.

**Por que duplicado:** o nginx serve `/static/` direto do disco
(`alias` no `nginx.conf`), sem passar pela aplicação — a fonte precisa
estar fisicamente dentro de `src/dashboard/static/`. O teste
`tests/test_identidade_dashboard.py` garante que estas cópias são
byte a byte idênticas às dos relatórios: se uma atualizar sem a outra,
o CI acusa.
