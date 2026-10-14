# Map

Every establishment with a coordinate, on swisstopo's own base map. Markers are coloured by fit band, and each one opens the company.

## The markers

A company has no location; its establishments do. A foundation running a home, a day centre and three residences is one legal entity at five places, so the map draws establishments. The seat arrives from the register through the geocode stage; other sites are typed on the client's card and placed as they are saved.

A marker placed on a town rather than on an address is drawn hollow and says so when clicked. The federal address search calls everything it returns an address, including the building it picks when the query was only a postcode, so each answer is compared with what was asked: the postcode must agree or nothing is stored, and a point is called an address only when the street and the house number are in the answer too.

Clicking a marker shows the name, the site, the address, the fit and the pipeline state, with a link to the company.

## The filters

- Canton
- Sector
- Segment
- Fit band
- Pipeline state

The legend under the filters says how many of the placed establishments are shown.

## The notices

The page says what is not yet drawn:

- how many companies hold a postal address from the register and have not been placed, with a link to the Put them on the map card
- whether the cantonal boundaries are held, with a link to the Draw the cantons card

## Where the markers come from

- Put them on the map, on Operations, asks the Confederation's address search where each registered address is. Free, one request per company, no key. The card can be limited to the companies on the Board, which is a few dozen requests rather than a few thousand.
- Draw the cantons fetches the cantonal outlines from swisstopo once. Free.
- A site added on a client's card is placed as it is saved.

Both the base map and the geocoder are swisstopo, published as open government data on the condition that the source is named, which the page does. Nothing is sent to a commercial geocoder.

## Related pages

- [Clients](clients.md), where the other sites of a client are typed
- [Operations](operations.md), where the two geo stages are run
