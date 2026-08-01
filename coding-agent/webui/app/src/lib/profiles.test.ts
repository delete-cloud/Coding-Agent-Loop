// @vitest-environment jsdom

import { describe, expect, it } from "vitest";
import {
  activeProfile,
  addProfile,
  deriveName,
  LEGACY_CONFIG_LS_KEY,
  loadProfiles,
  PROFILES_LS_KEY,
  removeProfile,
  setActiveProfile,
  updateProfile,
  type ProfileStore,
} from "./profiles";

function fakeStorage(initial: Record<string, string> = {}) {
  const map = new Map<string, string>(Object.entries(initial));
  return {
    getItem: (key: string) => map.get(key) ?? null,
    setItem: (key: string, value: string) => void map.set(key, value),
    removeItem: (key: string) => void map.delete(key),
    map,
  };
}

const profile = (id: string, name = id) => ({
  id,
  name,
  baseUrl: `http://${id}.test`,
  apiKey: "",
});

describe("loadProfiles", () => {
  it("seeds a same-origin active profile when nothing is stored", () => {
    const storage = fakeStorage();
    const store = loadProfiles(storage);

    expect(store.profiles).toHaveLength(1);
    expect(store.profiles[0].baseUrl).toBe(window.location.origin);
    expect(store.profiles[0].apiKey).toBe("");
    expect(store.activeId).toBe(store.profiles[0].id);
    // The seed is persisted so the next load is stable.
    expect(storage.map.get(PROFILES_LS_KEY)).toBe(JSON.stringify(store));
  });

  it("migrates the legacy config baseUrl/apiKey into a first profile", () => {
    const storage = fakeStorage({
      [LEGACY_CONFIG_LS_KEY]: JSON.stringify({
        baseUrl: "http://127.0.0.1:18080",
        apiKey: "secret-key",
      }),
    });
    const store = loadProfiles(storage);

    expect(store.profiles).toHaveLength(1);
    expect(store.profiles[0]).toMatchObject({
      name: "127.0.0.1:18080",
      baseUrl: "http://127.0.0.1:18080",
      apiKey: "secret-key",
    });
    expect(store.activeId).toBe(store.profiles[0].id);
  });

  it("loads a stored store verbatim and repairs a dangling activeId", () => {
    const a = profile("a");
    const b = profile("b");
    const storage = fakeStorage({
      [PROFILES_LS_KEY]: JSON.stringify({ profiles: [a, b], activeId: "gone" }),
    });
    const store = loadProfiles(storage);

    expect(store.profiles).toEqual([a, b]);
    expect(store.activeId).toBe("a");
  });

  it("reseeds when the stored payload is corrupt", () => {
    const storage = fakeStorage({ [PROFILES_LS_KEY]: "{not json" });
    const store = loadProfiles(storage);

    expect(store.profiles).toHaveLength(1);
    expect(store.profiles[0].baseUrl).toBe(window.location.origin);
  });

  it("reseeds when the stored store has no usable profiles", () => {
    const storage = fakeStorage({
      [PROFILES_LS_KEY]: JSON.stringify({ profiles: [{ name: "no url" }], activeId: "x" }),
    });
    const store = loadProfiles(storage);

    expect(store.profiles).toHaveLength(1);
    expect(store.profiles[0].baseUrl).toBe(window.location.origin);
  });
});

describe("profile CRUD", () => {
  const base = (): ProfileStore => ({
    profiles: [profile("a"), profile("b")],
    activeId: "a",
  });

  it("adds a profile with a derived name when the name is blank", () => {
    const store = addProfile(base(), {
      name: " ",
      baseUrl: "https://agent.example.com/",
      apiKey: "k",
    });

    expect(store.profiles).toHaveLength(3);
    const added = store.profiles[2];
    expect(added.name).toBe("agent.example.com");
    expect(added.baseUrl).toBe("https://agent.example.com/");
    expect(added.apiKey).toBe("k");
    // Adding never steals the active slot.
    expect(store.activeId).toBe("a");
  });

  it("updates fields of an existing profile only", () => {
    const store = updateProfile(base(), "b", { name: "renamed", apiKey: "k2" });

    expect(store.profiles[1]).toMatchObject({ id: "b", name: "renamed", apiKey: "k2" });
    expect(store.profiles[0]).toEqual(profile("a"));
  });

  it("switches the active profile", () => {
    const store = setActiveProfile(base(), "b");

    expect(store.activeId).toBe("b");
    expect(activeProfile(store)?.name).toBe("b");
  });

  it("ignores switching to an unknown id", () => {
    const store = setActiveProfile(base(), "nope");
    expect(store.activeId).toBe("a");
  });

  it("removes an inactive profile", () => {
    const store = removeProfile(base(), "b");
    expect(store.profiles.map((p) => p.id)).toEqual(["a"]);
    expect(store.activeId).toBe("a");
  });

  it("refuses to remove the active profile", () => {
    expect(() => removeProfile(base(), "a")).toThrow(/active/);
  });

  it("refuses to remove the last profile", () => {
    const single: ProfileStore = { profiles: [profile("only")], activeId: "only" };
    expect(() => removeProfile(single, "only")).toThrow(/last/);
  });
});

describe("deriveName", () => {
  it("uses the URL host", () => {
    expect(deriveName("http://localhost:8080/api")).toBe("localhost:8080");
  });

  it("falls back to 'default' for unparseable input", () => {
    expect(deriveName("not a url")).toBe("default");
  });
});
