// Tweaks panel for /secrets.

const TWEAK_DEFAULTS = /*EDITMODE-BEGIN*/{
  "revealMode": "eye",
  "defaultSort": "severity",
  "showVerifyCmd": true,
  "voiceParagraph": true
}/*EDITMODE-END*/;

function SecretsApp() {
  const [tweaks, setTweak] = window.useTweaks(TWEAK_DEFAULTS);

  // Expose globally so deep atoms (FingerprintRow) can read without prop-drilling.
  React.useEffect(() => {
    window.__revealMode    = tweaks.revealMode;
    window.__showVerifyCmd = tweaks.showVerifyCmd;
    window.__voiceParagraph = tweaks.voiceParagraph;
  }, [tweaks.revealMode, tweaks.showVerifyCmd, tweaks.voiceParagraph]);

  return (
    <>
      <window.DirectionPassport tweaks={tweaks} />
      <window.TweaksPanel title="Tweaks · /secrets">
        <window.TweakSection label="Privacy">
          <window.TweakRadio
            label="Reveal mode"
            value={tweaks.revealMode}
            options={[
              { value: 'eye',   label: 'eye' },
              { value: 'hover', label: 'hover' },
              { value: 'never', label: 'never' },
            ]}
            onChange={(v) => setTweak('revealMode', v)}
          />
          <window.TweakToggle
            label="Show verify cmd"
            value={tweaks.showVerifyCmd}
            onChange={(v) => setTweak('showVerifyCmd', v)}
          />
        </window.TweakSection>

        <window.TweakSection label="Spine">
          <window.TweakRadio
            label="Default sort"
            value={tweaks.defaultSort}
            options={[
              { value: 'severity', label: 'severity' },
              { value: 'recency',  label: 'recency'  },
              { value: 'alpha',    label: 'alpha'    },
            ]}
            onChange={(v) => setTweak('defaultSort', v)}
          />
        </window.TweakSection>

        <window.TweakSection label="Voice">
          <window.TweakToggle
            label="Voice paragraph"
            value={tweaks.voiceParagraph}
            onChange={(v) => setTweak('voiceParagraph', v)}
          />
        </window.TweakSection>
      </window.TweaksPanel>
    </>
  );
}

window.SecretsApp = SecretsApp;
