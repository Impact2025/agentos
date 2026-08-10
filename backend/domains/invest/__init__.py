"""Beursmeester — de portefeuille die écht bestaat.

`domains/finance` is de rapportagelaag: hij schrijft elke ochtend een advies en
mailt het. Dit domein is de tegenhanger die dat advies afrekent — posities,
grootboek, benchmark en trefkans. Advies zonder rekening is activiteit zonder
effect; hier krijgt het een rekening.

Niets in dit domein plaatst zelf een order. Elk voorstel landt op
`pending_review` in het Actiecentrum, precies zoals de Wachtrij voor content.
"""
