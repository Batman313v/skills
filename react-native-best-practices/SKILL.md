---
name: react-native-best-practices
description: "Comprehensive React Native best practices for production apps on the New Architecture, covering performance optimization (FPS, TTI, bundle size, memory), UI/UX patterns (animations, gestures, rich text), native development (JSI modules, multithreading, Turbo Modules), media (audio, SVG), and on-device AI. MUST USE before writing, reviewing, or debugging ANY code in a React Native or Expo project. If the working directory contains a package.json with react-native, expo, or expo-router as a dependency, this skill applies. Trigger on: any code task in a React Native/Expo project, 'React Native', 'Expo', 'New Architecture', 'Reanimated', 'Gesture Handler', 'react-native-svg', 'ExecuTorch', 'react-native-audio-api', 'react-native-enriched', 'react-native-enriched-markdown', 'Worklet', 'Fabric', 'TurboModule', 'WebGPU', 'react-native-wgpu', 'TypeGPU', 'GPU shader', 'WGSL', 'svg', 'animation', 'gesture', 'audio', 'rich text', 'AI model', 'multithreading', 'chart', 'vector', 'image filter', 'shared value', 'useSharedValue', 'runOnJS', 'scheduleOnUI', 'scheduleOnRN', 'thread', 'worklet', 'JSI', 'jsi::Runtime', 'HostObject', 'react-native-executorch', or any question involving UI, graphics, native modules, threading, animations, performance profiling, bundle size, FPS optimization, memory leaks, TTI, or React Native architecture. Also use when a more specific sub-skill matches."
license: MIT
metadata:
  author: Callstack / Software Mansion
  tags: react-native, expo, performance, optimization, animations, gestures, audio, svg, ai, multithreading, jsi, profiling
---

# React Native Best Practices

Comprehensive production patterns for React Native apps on the New Architecture. Combines performance optimization guidelines with UI/UX, native development, media, and AI best practices.

## When to Apply

Reference these guidelines when:
- Debugging slow/janky UI or animations
- Investigating memory leaks (JS or native)
- Optimizing app startup time (TTI)
- Reducing bundle or app size
- Writing native modules (Turbo Modules, JSI)
- Profiling React Native performance
- Building animations, gestures, audio, or rich text features
- Implementing on-device AI models
- Working with multithreading and worklets
- Reviewing React Native code for performance

## Security Notes

- Treat shell commands in these references as local developer operations. Review them before running, prefer version-pinned tooling, and avoid piping remote scripts directly to a shell.
- Treat third-party libraries and plugins as dependencies that still require normal supply-chain controls: pin versions, verify provenance, and update through your standard review process.
- If using Re.Pack code splitting, only load first-party chunks from trusted HTTPS origins tied to the current release.

## Priority-Ordered Guidelines

| Priority | Category | Impact | Prefix |
|----------|----------|--------|--------|
| 1 | FPS & Re-renders | CRITICAL | `js-*` |
| 2 | Bundle Size | CRITICAL | `bundle-*` |
| 3 | TTI Optimization | HIGH | `native-*`, `bundle-*` |
| 4 | Native Performance | HIGH | `native-*`, `jsi/*` |
| 5 | Memory Management | MEDIUM-HIGH | `js-*`, `native-*` |
| 6 | Animations & Gestures | MEDIUM | `animations/*`, `gestures/*`, `js-*` |
| 7 | Media, AI & Advanced | MEDIUM | `audio/*`, `svg/*`, `on-device-ai/*`, `multithreading/*`, `rich-text/*` |

Impact labels are triage hints: CRITICAL first, HIGH next, MEDIUM when evidence points there.

## Quick Reference

### Optimization Workflow

Follow this cycle for any performance issue: **Measure → Optimize → Re-measure → Validate**

1. **Measure**: Capture baseline metrics before changes. For runtime issues, prefer commit timeline, re-render counts, slow components, heaviest-commit breakdown, and startup/TTI when available. Component tree depth or count are optional context, not substitutes. Do not recommend memoization, atomic state, or compiler changes without a measured render or FPS problem.
2. **Optimize**: Apply the targeted fix from the relevant reference
3. **Re-measure**: Run the same measurement to get updated metrics
4. **Validate**: Confirm improvement (e.g., FPS 45→60, TTI 3.2s→1.8s, bundle 2.1MB→1.6MB)

If metrics did not improve, revert and try the next suggested fix.

### Review Guardrails

- Check library versions before suggesting API-specific fixes. Example: FlashList v2 deprecates `estimatedItemSize`, so do not flag it as missing there.
- Do not suggest `useMemo` or `useCallback` dependency changes unless behavior is demonstrably incorrect or profiling shows wasted work tied to that value.
- Do not report stale closures speculatively. Show the stale read path, a repro, or profiler evidence before calling it out.
- When profiling a flow, measure the target interaction itself. Do not treat component tree depth or component count as the main performance evidence.

### Critical: FPS & Re-renders

**Profile first:**
```bash
agent-device react-devtools status
agent-device react-devtools wait --connected
agent-device react-devtools profile start
agent-device react-devtools profile stop
agent-device react-devtools profile slow --limit 5
agent-device react-devtools profile rerenders --limit 5
agent-device react-devtools profile timeline --limit 20
```

Drive the target interaction with normal `agent-device` commands between `profile start` and `profile stop`.

Manual fallback when `agent-device` is unavailable: open React Native DevTools from Metro (`j`) or the Dev Menu, use the Profiler tab, and record the same interaction.

For release-build React component profiling, connect [`@callstack/inspector`](https://github.com/callstackincubator/inspector#inspector) first so React DevTools can attach to the release app, then run the `agent-device react-devtools` flow above.

**Common fixes:**
- Replace ScrollView with FlatList/FlashList/Legend List for long lists
- After profiling shows cascading re-renders, use React Compiler for automatic memoization
- After profiling shows broad store/context updates, use atomic state (Jotai/Zustand) to reduce re-renders
- Use `useDeferredValue` for expensive computations

### Critical: Bundle Size

**Analyze bundle:**
```bash
npx react-native bundle \
  --entry-file index.js \
  --bundle-output output.js \
  --platform ios \
  --sourcemap-output output.js.map \
  --dev false --minify true

npx source-map-explorer output.js --no-border-checks
```

**Verify improvement after optimization:**
```bash
# Record baseline size before changes
ls -lh output.js  # e.g., Before: 2.1 MB

# After applying fixes, re-bundle and compare
npx react-native bundle --entry-file index.js --bundle-output output.js \
  --platform ios --dev false --minify true
ls -lh output.js  # e.g., After: 1.6 MB  (24% reduction)
```

**Common fixes:**
- Avoid barrel imports (import directly from source)
- Remove unnecessary Intl polyfills only after checking Hermes API and method coverage
- Evaluate tree shaking (Expo SDK 52+ experimental unused import/export removal, or Re.Pack only if already configured)
- Enable R8 for Android native code shrinking

### High: TTI Optimization

**Measure TTI:**
- Use `react-native-performance` for markers
- Only measure cold starts (exclude warm/hot/prewarm)

**Common fixes:**
- For React Native 0.78 and earlier, disable Android JS bundle compression to enable Hermes mmap
- Use native navigation (react-native-screens)
- Preload commonly-used expensive screens before navigating to them

### High: Native Performance

**Profile native:**
- iOS: Xcode Instruments → Time Profiler
- Android: Android Studio → CPU Profiler

**Common fixes:**
- Use background threads for heavy native work
- Prefer async over sync Turbo Module methods
- Use C++ for cross-platform performance-critical code

## References

Full documentation with code examples in [references/][references]:

### JavaScript/React Performance (`js-*`)

| File | Impact | Description |
|------|--------|-------------|
| [js-lists-flatlist-flashlist.md][js-lists-flatlist-flashlist] | CRITICAL | Replace ScrollView with virtualized lists |
| [js-profile-react.md][js-profile-react] | MEDIUM | `agent-device react-devtools` profiling |
| [js-measure-fps.md][js-measure-fps] | HIGH | FPS monitoring and measurement |
| [js-memory-leaks.md][js-memory-leaks] | MEDIUM | JS memory leak hunting |
| [js-atomic-state.md][js-atomic-state] | HIGH | Jotai/Zustand patterns |
| [js-concurrent-react.md][js-concurrent-react] | HIGH | useDeferredValue, useTransition |
| [js-react-compiler.md][js-react-compiler] | HIGH | Automatic memoization |
| [js-animations-reanimated.md][js-animations-reanimated] | MEDIUM | Reanimated worklets (performance) |
| [js-bottomsheet.md][js-bottomsheet] | HIGH | Bottom sheet optimization |
| [js-uncontrolled-components.md][js-uncontrolled-components] | HIGH | TextInput optimization |

### Native (`native-*`)

| File | Impact | Description |
|------|--------|-------------|
| [native-turbo-modules.md][native-turbo-modules] | HIGH | Building fast native modules |
| [native-sdks-over-polyfills.md][native-sdks-over-polyfills] | HIGH | Native vs JS libraries |
| [native-measure-tti.md][native-measure-tti] | HIGH | TTI measurement setup |
| [native-threading-model.md][native-threading-model] | HIGH | Turbo Module threads |
| [native-profiling.md][native-profiling] | MEDIUM | Xcode/Android Studio profiling |
| [native-platform-setup.md][native-platform-setup] | MEDIUM | iOS/Android tooling guide |
| [native-view-flattening.md][native-view-flattening] | MEDIUM | View hierarchy debugging |
| [native-memory-patterns.md][native-memory-patterns] | MEDIUM | C++/Swift/Kotlin memory patterns |
| [native-memory-leaks.md][native-memory-leaks] | MEDIUM | Native memory leak hunting |
| [native-android-16kb-alignment.md][native-android-16kb-alignment] | CRITICAL | Third-party library alignment for Google Play |

### Bundling (`bundle-*`)

| File | Impact | Description |
|------|--------|-------------|
| [bundle-barrel-exports.md][bundle-barrel-exports] | CRITICAL | Avoid barrel imports |
| [bundle-analyze-js.md][bundle-analyze-js] | CRITICAL | JS bundle visualization |
| [bundle-tree-shaking.md][bundle-tree-shaking] | HIGH | Dead code elimination (Expo SDK 52+ experimental, Re.Pack) |
| [bundle-analyze-app.md][bundle-analyze-app] | HIGH | App size analysis |
| [bundle-r8-android.md][bundle-r8-android] | HIGH | Android R8 native code shrinking |
| [bundle-hermes-mmap.md][bundle-hermes-mmap] | HIGH | Disable Android bundle compression for Hermes mmap |
| [bundle-native-assets.md][bundle-native-assets] | HIGH | Asset catalog setup (iOS/Android) |
| [bundle-library-size.md][bundle-library-size] | MEDIUM | Evaluate dependency size impact |
| [bundle-code-splitting.md][bundle-code-splitting] | MEDIUM | Re.Pack code splitting |

### Animations (`animations/*`)

Production animation patterns for React Native using Reanimated 4, Skia, WebGPU, and TypeGPU. CSS transitions, shared value animations, canvas animations with react-native-skia, GPU shader animations, layout animations (entering/exiting/transitions/keyframes), scroll-driven animations, interpolation, particle systems, procedural noise, SDF rendering, performance tuning (120fps), accessibility (`useReducedMotion`).

| File | When to read |
|------|-------------|
| [animations/SKILL.md][animations-skill] | Master guide: choosing CSS transitions vs CSS animations vs shared value animations; core hooks (useSharedValue, useAnimatedStyle); animation functions (withSpring, withTiming, withDecay); scheduling on RN thread |
| [animations/animation-functions.md][animation-functions] | Gotchas for `withSpring` config modes; `withRepeat`, `withClamp`; composing animations |
| [animations/layout-animations.md][layout-animations] | Entering/exiting animation gotchas (`nativeID` conflict, view flattening); layout transitions; keyframe rules; list item animations (`itemLayoutAnimation`); shared element transitions |
| [animations/scroll-and-events.md][scroll-animations] | Scroll-driven patterns (`useAnimatedScrollHandler`, `scrollTo`, `useScrollOffset`); `useAnimatedReaction`; `useFrameCallback`; `measure` rules |
| [animations/canvas-animations.md][canvas-animations] | Canvas animations with react-native-skia; shared values as direct props; `interpolateColors`; Picture API; path animations (`usePathInterpolation`, `usePathValue`); SKSL runtime shaders and image filters |
| [animations/gpu-animations.md][gpu-animations] | GPU shader animations; WebGPU setup; TypeGPU typed pipelines; compute pipelines for particle systems, physics, simulations; `@typegpu/noise` (Perlin noise, PRNG); `@typegpu/sdf`; Three.js / React Three Fiber |
| [animations/canvas-atlas.md][canvas-atlas] | Atlas for batched sprite/tile animation; `useTexture`, `useRSXformBuffer`; RSXform matrix format |
| [animations/animations-performance.md][animation-performance] | Performance tuning; 120fps setup; feature flags; FPS drop fixes; simultaneous animation limits; accessibility (`useReducedMotion`, `ReducedMotionConfig`) |

### Gestures (`gestures/*`)

Touch interaction patterns with Gesture Handler v3. Never suggest `PanResponder` when RNGH is available — it runs on the JS thread and is effectively deprecated. `GestureHandlerRootView` is mandatory as an ancestor; import ScrollView/FlatList from `react-native-gesture-handler`. Never call JS-thread functions directly from gesture callbacks — wrap in `scheduleOnRN` from `react-native-worklets`.

| File | When to read |
|------|-------------|
| [gestures/SKILL.md][gestures-skill] | Complete guide: version decision tree (v2 builder API vs v3 hook API), critical rules, threading model, `GestureStateManager`, cross-component gestures (`VirtualGestureDetector`) |
| [gestures/tap-handling.md][tap-handling] | `RectButton`/`Pressable`/`Touchable`, tappable items in scroll containers, tap gestures, double-tap, hit slop |
| [gestures/gestures.md][gestures-core] | Pan (drag), Pinch (zoom), Rotation, Long press, Fling (swipe), Hover; offset accumulation; velocity and decay |
| [gestures/swipeable-and-drawer.md][swipeable] | `ReanimatedSwipeable` for list item actions; `ReanimatedDrawerLayout` for side menus; web scroll compatibility |
| [gestures/gesture-composition.md][gesture-composition] | Combining gestures (`Simultaneous`/`Race`/`Exclusive`); cross-component relations; Pan inside ScrollView |
| [gestures/continuous-gestures.md][continuous-gestures] | Continuous interaction patterns, Reanimated integration |
| [gestures/testing.md][gesture-testing] | Jest setup and mocking; `fireGestureHandler`; troubleshooting gesture conflicts |
| [gestures/v2-to-v3-migration.md][v2-migration] | Migration guide from v2 to v3; API changes; hook usage; memoization |

### SVG (`svg/*`)

Vector graphics with react-native-svg. For API details, webfetch https://github.com/software-mansion/react-native-svg/blob/main/USAGE.md.

| File | When to read |
|------|-------------|
| [svg/SKILL.md][svg-skill] | Complete SVG guide: when to use SVG vs Skia vs Lottie vs expo-image |
| [svg/svg.md][svg-guide] | Loading SVGs from URIs, XML strings, or files; filters; touch events; performance considerations |

### Audio (`audio/*`)

Real-time audio with react-native-audio-api. Single `AudioContext` singleton pattern; single `AudioRecorder` instance reuse; suspend when idle; `AudioBufferSourceNode` is single-use (create new node to replay); use `AudioParam` scheduling for smooth changes (no audible clicks).

| File | When to read |
|------|-------------|
| [audio/SKILL.md][audio-skill] | Complete audio guide: decision tree, AudioContext lifecycle, singleton pattern, audio graph concepts, decoding audio data, AudioBuffer state management |
| [audio/playback.md][playback] | `AudioBufferSourceNode`, `OscillatorNode`, `StreamerNode`, `AudioBufferQueueSourceNode`; looping and scheduling; playback rate and pitch; noise generation |
| [audio/recording.md][recording] | Recording audio with `AudioRecorder`; three recording modes (file, data callback, graph processing); file output configuration; permissions; background recording |
| [audio/effects-and-analysis.md][effects] | Effects chain (`GainNode`, `BiquadFilterNode`, `DelayNode`, `ConvolverNode`, `StereoPannerNode`, `WaveShaperNode`); ADSR envelopes; `AnalyserNode`; visualization patterns |
| [audio/worklets.md][worklets] | Audio worklets with `WorkletNode`, `WorkletSourceNode`, `WorkletProcessingNode`; `UIRuntime` vs `AudioRuntime`; performance budgets and latency constraints; custom synthesis |
| [audio/system-and-notifications.md][system-audio] | `AudioManager` for session configuration; `PlaybackNotificationManager`/`RecordingNotificationManager`; permissions; interruption handling; testing with mocks |

### Multithreading (`multithreading/*`)

Background processing with react-native-worklets. Three runtimes: UI Runtime (main thread, native events/animations), Worker Runtime (custom threads, heavy computation), RN Runtime (JS thread, React state/navigation). Functions on Worklet Runtimes need `'worklet';` directive. Closures are copied not shared — mutating original after scheduling has no effect.

| File | When to read |
|------|-------------|
| [multithreading/SKILL.md][mt-skill] | Complete multithreading guide: runtime model, API decision tree (fire-and-forget vs async return vs sync return), critical rules, deprecated APIs (`runOnUI` → `scheduleOnUI`, `runOnJS` → `scheduleOnRN`) |
| [multithreading/threading-api.md][mt-api] | Scheduling work across runtimes, creating Worker Runtimes, sync vs async execution, migrating from deprecated APIs |
| [multithreading/shared-memory.md][mt-shared] | Passing data between runtimes, closures in worklets, `Serializable`, `Synchronizable`, shared mutable state |
| [multithreading/setup-and-advanced.md][mt-setup] | Installing worklets, Babel plugin config, Bundle Mode, testing with Jest, feature flags, troubleshooting |

### JSI Native Modules (`jsi/*`)

Zero-overhead C++/JS interop on the New Architecture. The JavaScript Interface (JSI) is a C++ API that lets native code interact directly with the JavaScript engine — reading/writing JS values, calling JS functions, exposing C++ objects to JS — without going through the old async bridge. Single-thread rule: write operations only from one thread; use `shared_ptr<jsi::Value>` pattern for non-copyable Values.

| File | When to read |
|------|-------------|
| [jsi/SKILL.md][jsi-skill] | Complete JSI guide: architecture overview, type system, when to use Pure JSI vs TurboModules vs Nitro Modules vs C++ core |
| [jsi/overview.md][jsi-overview] | JSI architecture, why `rt.global()`, sync vs async model, event loop absence, `queueMicrotask`/`drainMicrotasks`, `setRuntimeData`/`getRuntimeData` |
| [jsi/core-types.md][jsi-core] | JSI type system: `Value`, `PropNameID`, `HostFunction`, `HostObject`, `NativeState`, `WeakObject`, `Array`, `ArrayBuffer`, `BigInt`, `Scope` — constructors, ownership, lifetimes, GC behavior |
| [jsi/casting-and-serialization.md][jsi-cast] | `get`/`as` naming convention, string encoding (`createFromAscii` vs `createFromUtf8`), zero-copy access, C++↔JS type mapping, `ISerialization`, `folly::dynamic` |
| [jsi/threading-safety.md][jsi-threading] | Single-thread rule, write operations from multiple threads, destruction order, `Value` non-copyable + `shared_ptr` pattern, hot reload pitfalls, `WithRuntimeDecorator<AroundLock>` |
| [jsi/calling-js-and-async.md][jsi-call] | Installing `HostFunction` bindings, calling JS functions from C++, `callWithThis`, `callAsConstructor`, `invokeAsync` + `CallInvoker`, Promise resolve/reject patterns, `JSIException` hierarchy |
| [jsi/module-approaches.md][jsi-modules] | Choosing between Pure JSI, TurboModules, Nitro Modules, or pure C++ core with thin adapter — decision tree, trade-offs, boilerplate comparison |
| [jsi/cpp-memory-patterns.md][jsi-memory] | C++ memory for JS developers: `unique_ptr`, `shared_ptr`, `std::move`, lambda captures, RAII, circular ownership, GC boundary between JS heap and native heap |
| [jsi/performance.md][jsi-performance] | Batching sequential calls, caching `PropNameID`, zero-copy `ArrayBuffer` via `MutableBuffer`, `setExternalMemoryPressure`, `Scope` in tight loops, avoiding `evaluateJavaScript` for function calls |
| [jsi/setup-and-templates.md][jsi-setup] | Installing JSI bindings in Android (FBJNI, JSIModulePackage) and iOS (ObjC++ `.mm`), scaffolding templates |
| [jsi/debugging-and-pitfalls.md][jsi-debug] | Crash traces, symbolication (`c++filt`, `addr2line`, `atos`, `ndk-stack`), ASan, common JSI crash patterns, pre-ship checklist |

### On-Device AI (`on-device-ai/*`)

AI models on-device with React Native ExecuTorch. Call `initExecutorch()` at app entry before any other API; check `isReady` before inference; interrupt LLM generation before unmounting to avoid crashes; use quantized model variants (`_QUANTIZED`); audio for STT/VAD must be 16 kHz mono (decode with `new AudioContext({ sampleRate: 16000 })`); TTS output is 24 kHz. New Architecture (Fabric) required; Expo Go unsupported.

| File | When to read |
|------|-------------|
| [on-device-ai/SKILL.md][ai-skill] | Complete AI guide: decision tree for all hooks (`useLLM`, `useClassification`, `useObjectDetection`, `useOCR`, `useSemanticSegmentation`, etc.), critical rules, hook quick reference, common pitfalls table |
| [on-device-ai/llm.md][ai-llm] | `useLLM` functional + managed modes, tool calling, structured output (JSON Schema / Zod), interrupting generation, vision-language models (LFM2_VL_*), generation config |
| [on-device-ai/vision.md][ai-vision] | Image classification, object detection, semantic + instance segmentation, pose estimation (COCO 17-keypoint), OCR (horizontal + vertical), style transfer, text-to-image, image + text embeddings |
| [on-device-ai/speech.md][ai-speech] | Speech-to-text (Whisper batch + streaming with timestamps), text-to-speech (Kokoro batch + streaming, phoneme input, voice catalogue), voice activity detection, audio sample-rate requirements |
| [on-device-ai/setup.md][ai-setup] | `initExecutorch`, Expo / bare resource-fetcher adapters (`ExpoResourceFetcher` / `BareResourceFetcher`), model loading strategies, Metro config for `.pte` files, error codes and recovery, `useTokenizer`, `usePrivacyFilter`, custom `.pte` models with `useExecutorchModule` |

### Rich Text (`rich-text/*`)

Rich text editing and rendering. Two libraries: `react-native-enriched` (editing via `EnrichedTextInput` — uncontrolled native input) and `react-native-enriched-markdown` (display via `EnrichedMarkdownText` — CommonMark-compliant parsing with md4c). Both require New Architecture (Fabric).

| File | When to read |
|------|-------------|
| [rich-text/SKILL.md][rt-skill] | Complete rich text guide: choosing the right library, editor API (toggling styles via ref, style detection via `onChangeState`, HTML output, mentions, inline images), Markdown renderer (flavors: commonmark/github, GFM tables, task lists, LaTeX math, custom styles) |

## Problem → Skill Mapping

| Problem | Start With |
|---------|------------|
| App feels slow/janky | [js-measure-fps.md][js-measure-fps] → [js-profile-react.md][js-profile-react] |
| Too many re-renders | [js-profile-react.md][js-profile-react] → [js-react-compiler.md][js-react-compiler] |
| Slow startup (TTI) | [native-measure-tti.md][native-measure-tti] → [bundle-analyze-js.md][bundle-analyze-js] |
| Large app size | [bundle-analyze-app.md][bundle-analyze-app] → [bundle-r8-android.md][bundle-r8-android] |
| Memory growing | [js-memory-leaks.md][js-memory-leaks] or [native-memory-leaks.md][native-memory-leaks] |
| Animation drops frames | [js-animations-reanimated.md][js-animations-reanimated] → `animations/SKILL.md` |
| Bottom sheet jank/re-renders | [js-bottomsheet.md][js-bottomsheet] → [js-animations-reanimated.md][js-animations-reanimated] |
| List scroll jank | [js-lists-flatlist-flashlist.md][js-lists-flatlist-flashlist] |
| TextInput lag | [js-uncontrolled-components.md][js-uncontrolled-components] |
| Native module slow | [native-turbo-modules.md][native-turbo-modules] → [native-threading-model.md][native-threading-model] |
| Tap/pan/pinch/swipe gestures | `gestures/SKILL.md` |
| Audio playback/recording/effects/worklets | `audio/SKILL.md` |
| SVG/vector graphics/charts/icons | `svg/SKILL.md` |
| On-device AI (LLM, vision, speech) | `on-device-ai/SKILL.md` |
| Multithreading / worklets / background processing | `multithreading/SKILL.md` |
| Rich text editor or Markdown rendering | `rich-text/SKILL.md` |
| C++ native modules (JSI bindings) | `jsi/SKILL.md` |
| Native library alignment issue | [native-android-16kb-alignment.md][native-android-16kb-alignment] |

[references]: references/

### JavaScript/React References
[js-lists-flatlist-flashlist]: references/js-lists-flatlist-flashlist.md
[js-profile-react]: references/js-profile-react.md
[js-measure-fps]: references/js-measure-fps.md
[js-memory-leaks]: references/js-memory-leaks.md
[js-atomic-state]: references/js-atomic-state.md
[js-concurrent-react]: references/js-concurrent-react.md
[js-react-compiler]: references/js-react-compiler.md
[js-animations-reanimated]: references/js-animations-reanimated.md
[js-bottomsheet]: references/js-bottomsheet.md
[js-uncontrolled-components]: references/js-uncontrolled-components.md

### Native References
[native-turbo-modules]: references/native-turbo-modules.md
[native-sdks-over-polyfills]: references/native-sdks-over-polyfills.md
[native-measure-tti]: references/native-measure-tti.md
[native-threading-model]: references/native-threading-model.md
[native-profiling]: references/native-profiling.md
[native-platform-setup]: references/native-platform-setup.md
[native-view-flattening]: references/native-view-flattening.md
[native-memory-patterns]: references/native-memory-patterns.md
[native-memory-leaks]: references/native-memory-leaks.md
[native-android-16kb-alignment]: references/native-android-16kb-alignment.md

### Bundling References
[bundle-barrel-exports]: references/bundle-barrel-exports.md
[bundle-analyze-js]: references/bundle-analyze-js.md
[bundle-tree-shaking]: references/bundle-tree-shaking.md
[bundle-analyze-app]: references/bundle-analyze-app.md
[bundle-r8-android]: references/bundle-r8-android.md
[bundle-hermes-mmap]: references/bundle-hermes-mmap.md
[bundle-native-assets]: references/bundle-native-assets.md
[bundle-library-size]: references/bundle-library-size.md
[bundle-code-splitting]: references/bundle-code-splitting.md

### Animation References
[animations-skill]: references/animations/SKILL.md
[animation-functions]: references/animations/animation-functions.md
[layout-animations]: references/animations/layout-animations.md
[scroll-animations]: references/animations/scroll-and-events.md
[gpu-animations]: references/animations/gpu-animations.md
[canvas-animations]: references/animations/canvas-animations.md
[canvas-atlas]: references/animations/canvas-atlas.md
[animation-performance]: references/animations/animations-performance.md

### Gesture References
[gestures-skill]: references/gestures/SKILL.md
[tap-handling]: references/gestures/tap-handling.md
[gestures-core]: references/gestures/gestures.md
[swipeable]: references/gestures/swipeable-and-drawer.md
[gesture-composition]: references/gestures/gesture-composition.md
[continuous-gestures]: references/gestures/continuous-gestures.md
[gesture-testing]: references/gestures/testing.md
[v2-migration]: references/gestures/v2-to-v3-migration.md

### SVG References
[svg-skill]: references/svg/SKILL.md
[svg-guide]: references/svg/svg.md

### Audio References
[audio-skill]: references/audio/SKILL.md
[playback]: references/audio/playback.md
[recording]: references/audio/recording.md
[effects]: references/audio/effects-and-analysis.md
[worklets]: references/audio/worklets.md
[system-audio]: references/audio/system-and-notifications.md

### Multithreading References
[mt-skill]: references/multithreading/SKILL.md
[mt-api]: references/multithreading/threading-api.md
[mt-shared]: references/multithreading/shared-memory.md
[mt-setup]: references/multithreading/setup-and-advanced.md

### JSI References
[jsi-skill]: references/jsi/SKILL.md
[jsi-overview]: references/jsi/overview.md
[jsi-core]: references/jsi/core-types.md
[jsi-cast]: references/jsi/casting-and-serialization.md
[jsi-threading]: references/jsi/threading-safety.md
[jsi-call]: references/jsi/calling-js-and-async.md
[jsi-modules]: references/jsi/module-approaches.md
[jsi-memory]: references/jsi/cpp-memory-patterns.md
[jsi-performance]: references/jsi/performance.md
[jsi-setup]: references/jsi/setup-and-templates.md
[jsi-debug]: references/jsi/debugging-and-pitfalls.md

### AI References
[ai-skill]: references/on-device-ai/SKILL.md
[ai-llm]: references/on-device-ai/llm.md
[ai-vision]: references/on-device-ai/vision.md
[ai-speech]: references/on-device-ai/speech.md
[ai-setup]: references/on-device-ai/setup.md

### Rich Text References
[rt-skill]: references/rich-text/SKILL.md

## Attribution

Based on "The Ultimate Guide to React Native Optimization" by Callstack and Software Mansion's production patterns for the New Architecture.
