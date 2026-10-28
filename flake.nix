{
  description = "Dev shell for ACCESS: FastAPI (Python) backend and Vite/React (Node) frontend";

  inputs = {
    nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";
    flake-utils.url = "github:numtide/flake-utils";
  };

  outputs =
    { nixpkgs, flake-utils, ... }:
    flake-utils.lib.eachDefaultSystem (
      system:
      let
        pkgs = import nixpkgs { inherit system; };
        inherit (pkgs) lib;

        # Toolchain versions are owned by other files in the repo (package.json,
        # tox.ini, the Makefile). Read them here at eval time rather than
        # duplicating the pins. Project dependencies themselves are still
        # installed by `make dev` and `npm install`, so requirements*.txt and
        # package-lock.json remain authoritative for those; Nix only supplies a
        # matching language toolchain.
        #
        # If a source file's format changes, the relevant parser below throws at
        # eval time instead of selecting a wrong version.

        # Look up a package attr (e.g. "python313") by name, throwing if nixpkgs
        # does not have it.
        pkgByName =
          what: attr:
          pkgs.${attr} or (
            throw "flake.nix: nixpkgs has no '${attr}' (derived for ${what}). "
            + "Either the canonical source moved to a version nixpkgs lacks, "
            + "or the nixpkgs input needs bumping."
          );

        # Return the first capture group of `re` applied to `text`, or throw.
        # `re` must hold one capture group and match the whole newline-flattened
        # text; callers include their own anchors. Matching the full string
        # rather than wrapping in `.*...*` keeps greedy backtracking from
        # splitting a multi-segment version like "22.12.0".
        matchOr =
          {
            file,
            re,
            hint,
          }:
          text:
          let
            m = builtins.match re (lib.replaceStrings [ "\n" ] [ " " ] text);
          in
          if m == null then
            throw "flake.nix: could not parse ${file} (${hint}). The dev shell derives a version from it; update the regex in flake.nix if the file's format changed."
          else
            builtins.head m;

        # Versions parsed from their source files.
        pkgJson = builtins.fromJSON (builtins.readFile ./package.json);
        engines =
          pkgJson.engines
            or (throw "flake.nix: package.json has no \"engines\" block to derive node/npm from.");
        nodeFloor = matchOr {
          file = "package.json engines.node";
          re = "[^0-9]*([0-9.]+).*"; # ">=22.12.0" -> "22.12.0"
          hint = "expected a semver like >=22.12.0";
        } engines.node;
        npmVersion = matchOr {
          file = "package.json engines.npm";
          re = "[^0-9]*([0-9.]+).*"; # ">=11.16.0" -> "11.16.0"
          hint = "expected a semver like >=11.16.0";
        } engines.npm;
        pyDigits = matchOr {
          file = "tox.ini envlist";
          re = ".*py[{]?([0-9]+).*"; # "py{313}" -> "313"
          hint = "expected an env like py313 or py{313}";
        } (builtins.readFile ./tox.ini);
        pgMajor = matchOr {
          file = "Makefile";
          re = ".*postgres:([0-9]+).*"; # "postgres:16" -> "16"
          hint = "expected a docker image ref like postgres:16";
        } (builtins.readFile ./Makefile);

        # Resolved toolchain packages.
        python = pkgByName "Python from tox.ini (py${pyDigits})" "python${pyDigits}";
        postgresql = pkgByName "Postgres from Makefile (postgres:${pgMajor})" "postgresql_${pgMajor}";

        # nixpkgs is selected by major and supplies the patch, so also check its
        # version against engines.node's full floor: a raised floor the pinned
        # nixpkgs can't meet then fails at eval rather than yielding an old Node.
        nodejs =
          let
            major = lib.versions.major nodeFloor;
            pkg = pkgByName "Node from package.json engines.node (>=${nodeFloor})" "nodejs_${major}";
          in
          lib.throwIf (!lib.versionAtLeast pkg.version nodeFloor) (
            "flake.nix: nixpkgs nodejs_${major} is ${pkg.version}, but package.json "
            + "engines.node requires >=${nodeFloor}. Bump the nixpkgs input to one "
            + "that ships a new enough Node ${major}.x."
          ) pkg;
      in
      {
        devShells.default = pkgs.mkShell {
          name = "access-dev";

          packages = with pkgs; [
            # --- Backend toolchain ---
            python
            python.pkgs.pip
            python.pkgs.virtualenv

            # --- Frontend toolchain ---
            nodejs # major derived from package.json engines.node; npm bumped in-shell

            # --- Database (for `make pytest-postgres`, alembic, psql) ---
            postgresql # major derived from the Makefile's postgres:NN image

            # --- Build / dev utilities ---
            gnumake
            git
            docker-client # `docker` / `docker compose` CLI for the make docker targets

            # --- Build deps for Python wheels that compile from source
            #     (cryptography, asyncpg, etc.) when no wheel matches ---
            stdenv.cc.cc.lib
            openssl
            libffi
            zlib
          ];

          env = {
            PIP_CONSTRAINT = "constraints.txt";
            # Let pip-built native extensions find the Nix-provided libs.
            LD_LIBRARY_PATH = lib.makeLibraryPath [
              pkgs.stdenv.cc.cc.lib
              pkgs.openssl
              pkgs.zlib
            ];
          };

          # Hook lives in a real .sh file so it stays shellcheck-able and free of
          # nixfmt's string reindentation; values it needs are passed as env vars.
          shellHook = ''
            export NPM_VERSION=${npmVersion}
            export PYTHON=${python.interpreter}
            source ${./nix/dev-shell-hook.sh}
          '';
        };
      }
    );
}
