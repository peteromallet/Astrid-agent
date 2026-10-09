// Generated element registries import each pack element as
// `@pack-<pack id>-elements-<kind>/<element>/component?astrid=<hash>`. The
// bundler aliases every in-tree and ASTRID_PACKS_PATH pack (webpack-alias.mjs),
// so this declaration only gives those imports a type for tsc. Packs with a
// typed declaration in src/astrid-hashed-imports.d.ts keep it: the longer
// prefix wins. The `any` is deliberate: the registry types each entry.
declare module '@pack-*' {
  const component: any;
  export default component;
}
