# The map

> The figures on this page were measured on the first deployment, in 2026, over a survey of five cantons of French-speaking Switzerland. Yours will differ.

Every establishment with a coordinate, on swisstopo's own base map. Markers
are coloured by fit band, filtered by canton, sector, segment, band and pipeline state,
and each one opens the company.

**A company has no location; its establishments do.** A foundation running an
EMS, a day centre and three residences is one legal entity at seven places, and
a single marker for it would be wrong in six of them. So `geo.establishment` is
a table: the seat arrives from the register through the `geocode` stage, and
the other sites are typed on the client page, where they are geocoded as they
are saved.

**A point is separate from the address it was made from.** An address is what
the register or the client told us; a point is what a geocoder made of it, at a
precision, on a date. A bad geocode over a good address is the ordinary case,
and one row could not tell them apart.

**The precision is graded here, not read from the reply.** This is the part
worth knowing before trusting the map. The federal search calls everything it
returns an address, including the arbitrary building it picks when the query
was only a postcode. Measured on 13.08.2026:

```
"Chemin du Petit-Flon 49, 1018 Lausanne"  ->  origin=address, Chemin du Petit-Flon 49
"1018 Lausanne"                           ->  origin=address, Rue Traversière 1
```

The second is a real building in the right postcode with nothing to do with the
question, and its `origin`, `rank` and `weight` are indistinguishable from the
first's. So each answer is compared with what was asked: the postcode must
agree or nothing is stored, and a point is called an address only when the
