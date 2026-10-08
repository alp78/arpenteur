# Limen, the German approach

For every canton `stages.draft.language_of` does not resolve to `fr` or `it`,
which is the German-speaking majority of the survey.

The first sentence has two variants, one per band. Keep the one that fits and
delete the other: telling a managing agent that the tool touches its weekly work
is exact, telling a landlord the same thing is not.

The product sentences restate the application's own page at
`src/content/apps/limen/de.mdx` in the PERINGER repository, in the owner's own
German rather than a translation of the French. Swiss orthography throughout, so
`ss` and never `ß`.

The anchor is `#testeurs` in every language, because `AppPage.astro` hardcodes
that id. It is not a French-only link.

---

Betreff: Limen, das Übergabeprotokoll auf dem Telefon: Zugang zum Test

Sehr geehrte Damen und Herren,

*[Band `regie-core`]* Ihr statutarischer Zweck nennt die Verwaltung von
Liegenschaften für Dritte, womit ein Übergabeprotokoll bei jedem Mieterwechsel
anfällt.

*[Band `regie-letting`]* Ihr statutarischer Zweck nennt die Vermietung von
Liegenschaften, eine Tätigkeit, die ein Einzugsprotokoll und später ein
Auszugsprotokoll verlangt.

Limen hält diese Übergabe auf einem einzigen Telefon fest, bis zum
unterschriebenen A4-PDF mit den Fotos als nummeriertem Anhang. Jede Feststellung
erhält eine Beschreibung, die Kostenzuordnung, eine Frist zur Behebung und die
erklärte Position der Gegenseite. Eine kantonale Ergänzung deckt die Regeln ab,
die sich innerhalb der Schweiz unterscheiden, darunter das Waadtländer RULV.

Die App verlangt kein Konto und keine Anmeldung bei mir. Das ganze Protokoll
bleibt auf dem Telefon. Die Adresssuche von swisstopo ist ihr einziger
Netzdienst, weshalb mich kein Protokollinhalt erreicht.

Limen läuft im geschlossenen Test auf Android wie auf dem iPhone, und der Test
kostet nichts.

Die Anmeldung läuft über das Formular auf der Seite der App:

https://peringer.ch/applications/limen/de/#testeurs

Auf Android ist die anzugebende Adresse das Google-Konto, das auf dem Telefon
schon eingerichtet ist, denn Google Play verlangt ein Konto für die Installation
einer Testfassung. Google verlangt überdies zwölf Testende, die vierzehn Tage
ohne Unterbruch angemeldet bleiben, bevor eine App in den Katalog kommt.

Auf dem iPhone ist die anzugebende Adresse die Ihrer Apple-ID, an die die
TestFlight-Einladung geht, ohne Verpflichtung Ihrerseits.

Möchten Sie, dass eine Ihrer Mitarbeiterinnen oder einer Ihrer Mitarbeiter Limen
bei einer nächsten Übergabe ausprobiert?

Über Limen hinaus entwickle ich mobile Anwendungen und Software nach Mass. Falls
Ihre Verwaltung einen eigenen Bedarf hat, den die Werkzeuge am Markt nicht
abdecken, kann ich Ihnen eine Studie dazu anbieten.

Freundliche Grüsse

Alexis Peringer
alexis@peringer.ch
https://peringer.ch

---

## What this message withholds

**The TestFlight link**

The iOS test build is waiting on Apple's Beta App Review. The public join link
already works while the build behind it stays unapproved, so the German page
withholds it exactly as the French one does and promises it by mail.

**Any price**

The message states that the test costs nothing. The published version's price
turns on an agreement that is not yet active, so no figure appears.

**Any saving in francs or hours**

No measurement over real handovers exists yet, which is what the test is meant
to produce.
