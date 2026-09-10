Responses recorded verbatim from the Flightradar24 API, never written by hand.

`live_position.json` holds one row exactly as `/live/flight-positions/full`
returned it. Until it exists the parsing tests in `test_fr24.py` are skipped: a
fixture invented from the specification would pass against itself and prove
nothing about the reply FR24 actually sends.
