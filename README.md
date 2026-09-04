# Invigi

A small browser-based study invigilator.

I made this because I kept getting distracted while studying and wanted something a bit stricter than a normal timer. The idea is pretty simple: keep the camera on, detect when I've actually stopped working, and make enough noise that I have to come back.

It runs in the browser and does the processing locally. There is no backend.

**Demo:** https://adtyamalpani99.github.io/invigi/

## What it does

Invigi currently looks at a few different things during a session:

* head/gaze direction
* whether you're looking at the screen, notebook, or textbook
* phone detection
* movement/flicker that looks like a phone screen
* sustained background audio
* whether you've left the desk
* how long a distraction has lasted

The important part is that none of these are treated as a single-frame decision. Everything goes through some amount of persistence / hysteresis / cooldown logic first.

For example, looking away for two seconds shouldn't necessarily mean anything. Looking away for a long time with no signs of reading is more interesting.

## How the detection works

### Face / gaze

Face tracking uses MediaPipe FaceLandmarker.

I use the face landmarks to estimate head direction and iris/gaze direction. The first part of a session is also used to learn where the person normally looks while studying.

The study-area model is an online Gaussian mixture model over the signed head/gaze angles.

So if your normal workflow is:

```text
screen -> notebook -> textbook -> screen
```

those directions become part of the expected study region instead of being treated as distractions.

### Reading detection

This was added because simple gaze thresholds gave too many false positives.

If you're reading a page, the eyes usually move around. If you're staring in one direction for a long time, there is much less movement.

So for an unfamiliar gaze direction, the detector checks the eye movement signal before escalating it.

It's not trying to decide whether you're literally reading a sentence. It's just trying to distinguish "looking at something and actively reading" from "staring at something".

### Phone detection

Phone detection uses two signals.

The first is EfficientDet-Lite0 with the COCO `cell phone` class.

The second is a small screen-flicker detector I wrote for the app. It looks for bright regions that change quickly across frames.

The two signals are useful for different reasons. Object detection can miss a partially visible phone, while the flicker detector can sometimes pick up a screen even when the object detector doesn't.

Both are filtered over time.

### Audio

Audio processing is intentionally basic.

I don't run speech recognition and I don't send audio anywhere.

The microphone input is reduced to things like RMS and frequency-band energy. A running estimate of the normal room noise is used as the baseline, and the detector looks for sustained loud audio.

The goal is basically to tell the difference between:

```text
one loud sound
```

and:

```text
something playing for a while
```

It isn't supposed to identify what someone is saying.

## Alerts

If the attention engine decides that you're actually distracted, the alert starts.

The alert is deliberately annoying: it continues until you return to the study area or stop it manually.

There is also a cooldown/re-arm system so one distraction doesn't turn into an endless sequence of new alerts.

Intentional breaks are tracked separately and aren't treated as distractions.

## Session engine

The session layer keeps track of the boring but important stuff:

* elapsed study time
* pauses
* breaks
* distraction events
* alert state
* Pomodoro sessions
* session recovery

The attention engine doesn't depend directly on `Date.now()`. A clock can be injected, which makes the state transitions easier to test.

## Stats

The stats page uses the events collected during the session.

It currently shows:

* focus score
* distraction history
* daily totals
* weekly totals
* session history
* a simple least-squares trend line

Nothing here is generated from fake sample data once a session has been recorded.

## Privacy

This was one of the requirements from the beginning.

Camera frames and microphone data are processed in memory and then discarded.

There is no account and no server involved in the normal web version.

Data that persists locally is basically:

```text
session summaries
gaze / angle values used by the app
settings
```

It's stored in browser localStorage.

You can export the stored data or wipe it from the settings page.

The ML models are downloaded from a public CDN when they're first needed. Inference itself runs on the device.

## Architecture

The project is intentionally small.

```text
camera
  |
  v
MediaPipe FaceLandmarker
  |
  +----> head pose / iris data
  |
  v
study-zone model
  |
  v
attention engine <---- phone detection
  ^                  <---- screen flicker
  |                  <---- audio analysis
  |
  v
session engine
  |
  v
alert engine
```

Most of the project currently lives in `index.html`. There isn't a frontend framework or build system.

That is intentional for now.

## Running it

### GitHub Pages / static

Open `index.html` in a browser or deploy the repository to GitHub Pages.

Chrome or Edge is recommended.

Camera and microphone access require a secure origin, so GitHub Pages or localhost is the easiest way to run it.

### Local

There is a tiny Python server included:

```bash
python steady.py
```

It only uses the Python standard library.

It serves the app locally and can store session data in SQLite.

No `pip install` is required.

## Tests

There is a small test suite built into the app.

Open:

```text
Settings -> Run tests
```

or run:

```js
window.invigiTests()
```

There are currently 27 assertions covering things like:

* attention state transitions
* timer accounting
* pauses and breaks
* study-zone learning
* phone detection paths
* absence detection
* audio triggers
* cooldown behaviour
* persistence
* analytics calculations

Most of these tests are for the state/engine code rather than the ML models themselves.

## Project structure

```text
invigi/
├── index.html
├── steady.py
├── LICENSE
└── README.md
```

`index.html` contains the UI, ML code, audio processing, session logic, attention state machine, and tests.

`steady.py` is only for the optional local server / SQLite mode.

## Limitations

This is still a browser app, so the detection isn't perfect.

Lighting, camera position, glasses, multiple people in frame, unusual study positions, and bad microphones can all affect detection.

The phone detector can also get it wrong. The persistence checks help, but they don't make the underlying model perfect.

The study-zone model also improves after it has seen enough normal study behaviour. The first few minutes of a session can therefore be less reliable.

## Roadmap

Not planning anything huge here yet.

Things I'd like to work on:

* better handling of difficult lighting
* better study-zone adaptation
* weekly summaries
* additional intervention languages
* optional sync between devices

## License

MIT
