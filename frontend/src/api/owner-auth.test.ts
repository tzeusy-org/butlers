import { describe, expect, it } from "vitest";
import { credentialWire, loginOptions, registrationOptions } from "./owner-auth";

const bytes = (size: number) => new Uint8Array(size).buffer;
const b64 = (size: number) => Buffer.from(bytes(size)).toString("base64url");
function nativeCredential(register = false): Credential {
  return { id: b64(32), rawId: bytes(32), type: "public-key", authenticatorAttachment: "platform",
    getClientExtensionResults: () => ({}),
    response: register ? { clientDataJSON: bytes(12), attestationObject: bytes(24), getTransports: () => ["internal"] }
      : { clientDataJSON: bytes(12), authenticatorData: bytes(37), signature: bytes(70), userHandle: bytes(32) },
  } as unknown as Credential;
}
describe("native WebAuthn wire boundary", () => {
  it("converts only binary options and emits exactly the admitted native response fields", () => {
    const publicKey = { challenge: b64(32), user: { id: b64(32), name: "owner", displayName: "Butlers owner" } };
    expect(new Uint8Array(registrationOptions({ ceremony_id: b64(32), publicKey }).challenge as ArrayBuffer)).toHaveLength(32);
    expect(new Uint8Array(loginOptions({ ceremony_id: b64(32), publicKey }).challenge as ArrayBuffer)).toHaveLength(32);
    const wire = credentialWire(nativeCredential(), "login");
    expect(Object.keys(wire).sort()).toEqual(["authenticatorAttachment", "clientExtensionResults", "id", "rawId", "response", "type"]);
    expect(Object.keys(wire.response as object).sort()).toEqual(["authenticatorData", "clientDataJSON", "signature", "userHandle"]);
    expect(Object.keys(credentialWire(nativeCredential(true), "register").response as object).sort()).toEqual(["attestationObject", "clientDataJSON", "transports"]);
  });
  it("rejects noncanonical base64, unknown extensions, mismatched IDs and missing handles before submission", () => {
    expect(() => loginOptions({ ceremony_id: b64(32), publicKey: { challenge: "AQ==" } })).toThrow();
    const credential = nativeCredential() as PublicKeyCredential;
    for (const overrides of [{ id: "different" }, { getClientExtensionResults: () => ({ unknown: true }) },
      { response: { ...credential.response, userHandle: null } }, { rawId: bytes(1024) }, { authenticatorAttachment: "unknown" }]) {
      expect(() => credentialWire({ ...credential, ...overrides } as unknown as Credential, "login")).toThrow();
    }
    const oversized = { ...nativeCredential(true), response: { clientDataJSON: bytes(12), attestationObject: bytes(65537) } };
    expect(() => credentialWire(oversized as unknown as Credential, "register")).toThrow();
  });
});
