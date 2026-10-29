"""Bounded neural speech with optional playback during sentence generation."""


class PhraseFailure(RuntimeError):
    def __init__(self, code, tokens=0, samples=0):
        super().__init__(code)
        self.code, self.tokens, self.samples = code, tokens, samples


def clone_phrase(generate, evaluate, reset, text, cancel, prepare=False, emit=None):
    """Keep linguistic context intact while optionally streaming checked PCM.

    Sampled decoding retains variation in rhythm and intonation. A bounded
    deterministic retry handles failures before playback starts. After publishing
    audio, failures must stop the stream: replaying a sentence would duplicate it.
    Without emit, validate the complete sentence before returning its PCM.
    """
    import numpy as np

    limit = max(128, min(720, len(text.split()) * 10 + 80))
    for attempt, temperature in enumerate((0.7, 0.0)):
        if cancel.is_set():
            raise InterruptedError()
        chunks, tokens, size, rate, published = [], 0, 0, None, False

        def pcm(samples):
            return (np.clip(samples, -1, 1) * 32767).astype("<i2").tobytes()

        def audible(samples):
            threshold = max(0.001, float(np.max(np.abs(samples))) * 0.005)
            return np.flatnonzero(np.abs(samples) > threshold)

        generator = generate(text=text, temperature=temperature, max_tokens=limit)
        try:
            for result in generator:
                if cancel.is_set():
                    raise InterruptedError()
                evaluate(result.audio)
                samples = np.asarray(result.audio, dtype=np.float32).reshape(-1)
                tokens += result.token_count
                size += samples.size
                if rate is not None and result.sample_rate != rate:
                    raise PhraseFailure("sample_rate_changed", tokens, size)
                rate = result.sample_rate
                if not np.isfinite(samples).all():
                    raise PhraseFailure("nonfinite_audio", tokens, size)
                if tokens >= limit or size > rate * 60:
                    raise PhraseFailure("generation_limit", tokens, size)
                if prepare:
                    return b"", rate, tokens, attempt
                if samples.size:
                    chunks.append(samples.copy())
                if emit is not None and chunks:
                    # Brief lookahead smooths delivery without requiring EOS.
                    # Keep a trailing chunk for end-of-sentence silence trimming.
                    pending_size = sum(chunk.size for chunk in chunks)
                    if pending_size < rate * (0.48 if published else 1.44):
                        continue
                    pending = np.concatenate(chunks)
                    if not published:
                        active = audible(pending)
                        if not active.size:
                            if pending.size > rate * 4:
                                raise PhraseFailure("empty_audio", tokens, size)
                            continue
                        pending = pending[max(0, int(active[0]) - rate * 80 // 1000) :]
                    end = max(0, pending.size - int(rate * 0.48))
                    if end:
                        published = True
                        emit(pcm(pending[:end]), rate)
                    chunks = [pending[end:].copy()]
            if cancel.is_set():
                raise InterruptedError()
            if not chunks:
                raise PhraseFailure("empty_audio", tokens, size)
            audio = np.concatenate(chunks)
            # Remove only quiet margins. Keep room around consonants/breaths;
            # interior pauses and all spoken content remain untouched.
            active = audible(audio)
            if not active.size:
                if not published:
                    raise PhraseFailure("empty_audio", tokens, size)
                start, end = 0, min(audio.size, rate * 240 // 1000)
            else:
                start = 0 if published else max(0, int(active[0]) - rate * 80 // 1000)
                end = min(audio.size, int(active[-1]) + 1 + rate * 240 // 1000)
            final_pcm = pcm(audio[start:end])
            if published:
                if final_pcm:
                    emit(final_pcm, rate)
                return b"", rate, tokens, attempt
            return final_pcm, rate, tokens, attempt
        except PhraseFailure:
            if attempt or published:
                raise
        finally:
            generator.close()
            reset()
    raise AssertionError("Unreachable")
