# Use case: open-pit mine monitoring from Sentinel-2

**Who uses it.** Government mining and geological agencies, and their partners. They need to know what is happening at licensed mines without visiting every site. Solafune's public mining work fits this pattern:
- ShigenAI is presented as a service for mineral exploration and for monitoring development activity.
- A UNIDO/METI programme in the DRC monitors open-pit excavation, extraction and stockpile changes.
- A 2026 MoU with Zambia's National Remote Sensing Centre names "mining activity monitoring" as its first focus area.

**Questions the product should answer** (in this order of value):
1. **Is the mine expanding, and where?** Footprint growth, new disturbance outside the known footprint.
2. **Which facilities are active?** Pits being deepened or widened, waste dumps growing, tailings deposition moving.
3. **Are there environmental flags?** Tailings facility water level, new water bodies, vegetation loss outside the mine, burning.
4. **What should be checked on the ground or with very-high-resolution imagery?** A short, prioritized list with coordinates.

**What it does not do.** It does not estimate production or ore grade, and it does not judge legality on its own: it has no licence boundaries. It cannot see underground or below 10 m resolution. Change is a *lead* for follow-up, not proof.

**How to talk to users.** Lead with the finding in plain language, then the number with units and period, then the caveat that matters. Avoid jargon such as "Mahalanobis d²" unless asked.
