class AudioProcessor extends AudioWorkletProcessor {
	constructor() {
		super();
		this.chunkSize = 2048;
		this.buffer = new Float32Array(this.chunkSize);
		this.offset = 0;
		this.port.postMessage({ type: "init", sampleRate });
	}

	_flush() {
		if (this.offset === 0) {
			return;
		}
		const chunk = this.buffer.slice(0, this.offset);
		this.port.postMessage({ type: "chunk", payload: chunk.buffer }, [chunk.buffer]);
		this.buffer = new Float32Array(this.chunkSize);
		this.offset = 0;
	}

	process(inputs) {
		const input = inputs[0]?.[0];
		if (!input || input.length === 0) {
			return true;
		}

		let inputOffset = 0;
		while (inputOffset < input.length) {
			const spaceRemaining = this.chunkSize - this.offset;
			const copyCount = Math.min(spaceRemaining, input.length - inputOffset);
			this.buffer.set(input.subarray(inputOffset, inputOffset + copyCount), this.offset);
			this.offset += copyCount;
			inputOffset += copyCount;

			if (this.offset >= this.chunkSize) {
				this._flush();
			}
		}

		return true;
	}
}

registerProcessor("audio-processor", AudioProcessor);