# Limen, the German approach

For every canton `stages.draft.language_of` does not resolve to `fr` or `it`,
which is the German-speaking majority of the survey.

Five movements, in this order, because a reader who does not know who is
writing has no reason to read what follows:

1. Who is writing, and why this sector.
2. What Limen is, as an object rather than a feature list.
3. How a handover runs with it, start to finish.
4. Why the sender believes it helps, stated as a belief.
5. How to enrol, and only then.

The first paragraph has two variants, one per band. Keep the one that fits and
delete the other.

The product sentences restate the application's own page at
`src/content/apps/limen/de.mdx` in the PERINGER repository, in the owner's own
German rather than a translation of the French. Swiss orthography throughout,
so `ss` and never `ß`.

No signature line appears below the closing. `arpenteur send` appends the
fragment from `signature.html`, edited at `/signature` in the console.

The anchor is `#testeurs` in every language, because `AppPage.astro` hardcodes
that id. It is not a French-only link.

---

Betreff: Limen, das Übergabeprotokoll auf dem Telefon

Sehr geehrte Damen und Herren,

Ich entwickle mobile Anwendungen als Selbständiger. Die Immobilienverwaltung
interessiert mich seit einiger Zeit, besonders das Übergabeprotokoll, einer der
letzten Vorgänge des Fachs, der noch auf Papier abläuft.

*[Band `regie-core`]* Ihr statutarischer Zweck nennt die Verwaltung von
Liegenschaften für Dritte, womit dieser Vorgang bei Ihnen zu jedem
Mieterwechsel anfällt.

*[Band `regie-letting`]* Ihr statutarischer Zweck nennt die Vermietung von
Liegenschaften, eine Tätigkeit, die ein Einzugsprotokoll und später ein
Auszugsprotokoll verlangt.

Limen ersetzt das Papierformular der Übergabe. Das Protokoll wird nicht mehr im
Büro abgeschrieben: es entsteht vor Ort auf einem Telefon und ist fertig, wenn
Sie die Wohnung verlassen.

Der Ablauf hat fünf Schritte:

1. Sie öffnen bei der Ankunft eine Sitzung und geben an, welche Partei Sie vertreten.
2. Sie gehen die Räume in der Reihenfolge durch, die Ihnen passt, beschreiben die Mängel und fotografieren sie.
3. Sie ergänzen die Unterlagen, darunter die Zählerstände und die Schlüsselübergabe.
4. Beide Parteien unterschreiben auf dem Telefon oder verweigern die Unterschrift mit Begründung.
5. Die App erzeugt das PDF-Protokoll, das jede Partei mitnimmt.

Drei Dinge ändern sich dadurch, dass das Protokoll vor Ort fertig wird:

1. Jedes Foto hängt schon an der Feststellung, die es zeigt.
2. Die Unterschrift wird eingeholt, solange beide Parteien noch vor Ort sind.
3. Die Unterlagen brauchen keine zweite Erfassung im Büro.

Eine Zahl zur Zeitersparnis nenne ich nicht, weil sie an echten Übergaben noch
nicht gemessen wurde. Genau das soll dieser Test leisten.

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

No measurement over real handovers exists yet, and the message says so in its
fourth movement rather than passing over it.
