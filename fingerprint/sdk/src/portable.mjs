function clone(value) {
  return structuredClone(value);
}

export function toPortableFingerprint(profile) {
  return {
    schemaVersion: profile.schemaVersion,
    id: profile.id,
    seed: profile.seed,
    preset: profile.preset,
    generator: clone(profile.generator),
    browser: {
      engine: profile.engine.family,
      version: profile.engine.version,
      userAgent: profile.engine.userAgent,
      userAgentMetadata: clone(profile.engine.userAgentMetadata)
    },
    operatingSystem: clone(profile.os),
    machine: clone(profile.machine),
    locale: clone(profile.locale),
    exposedApis: {
      navigator: clone(profile.navigator),
      screen: clone(profile.screen),
      canvas: clone(profile.canvas),
      audioContext: clone(profile.audioContext),
      clientRects: clone(profile.clientRects),
      webgl: {
        infoEnabled: profile.graphics.webglInfoEnabled,
        vendor: profile.graphics.webglVendor,
        renderer: profile.graphics.webglRenderer,
        noise: clone(profile.graphics.noise)
      },
      webgpu: clone(profile.graphics.webgpu),
      fonts: clone(profile.fonts),
      speechSynthesis: clone(profile.speechSynthesis),
      mediaDevices: clone(profile.mediaDevices),
      webrtc: clone(profile.webrtc),
      geolocation: clone(profile.geolocation),
      battery: clone(profile.battery),
      networkInformation: clone(profile.network),
      bluetooth: clone(profile.bluetooth)
    },
    browserBehavior: clone(profile.content),
    securityBehavior: clone(profile.security),
    runtimeEnvironment: clone(profile.runtime)
  };
}
