{
  description = "Hub Moon — parametric EQ control for Moondrop USB DACs (CLI + Qt GUI)";

  inputs.nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";

  outputs = { self, nixpkgs }:
    let
      systems = [ "x86_64-linux" "aarch64-linux" ];
      forAll = f: nixpkgs.lib.genAttrs systems (s: f nixpkgs.legacyPackages.${s});
    in
    {
      packages = forAll (pkgs: {
        default = pkgs.python3Packages.buildPythonApplication {
          pname = "hub-moon";
          version = "0.2.0";
          src = ./.;
          pyproject = true;

          build-system = [ pkgs.python3Packages.setuptools ];

          # runtime deps — the distro-native, non-bundled route
          dependencies = with pkgs.python3Packages; [ hidapi pyside6 ];

          # Qt apps must be wrapped so they find their QML/Qt plugins at runtime
          nativeBuildInputs = [ pkgs.qt6.wrapQtAppsHook ];
          dontWrapQtApps = false;

          # ship the udev rule (see nixosModules.default), desktop entry, icon
          postInstall = ''
            install -Dm644 packaging/70-moondrop.rules \
              $out/lib/udev/rules.d/70-moondrop.rules
            install -Dm644 packaging/hub-moon.desktop \
              $out/share/applications/hub-moon.desktop
            install -Dm644 packaging/hub-moon.svg \
              $out/share/icons/hicolor/scalable/apps/hub-moon.svg
          '';

          meta = with pkgs.lib; {
            description = "Parametric EQ control for Moondrop USB DACs";
            homepage = "https://github.com/MiyukiVigil/Hub_Moon";
            license = licenses.mit;
            mainProgram = "hub-moon-gui";
            platforms = platforms.linux;
          };
        };
      });

      # `nix run` → the GUI
      apps = forAll (pkgs: {
        default = {
          type = "app";
          program = "${self.packages.${pkgs.system}.default}/bin/hub-moon-gui";
        };
      });

      # NixOS users: imports.services.udev picks up the hidraw rule system-wide.
      #   imports = [ hub-moon.nixosModules.default ];
      nixosModules.default = { pkgs, ... }: {
        environment.systemPackages = [ self.packages.${pkgs.system}.default ];
        services.udev.packages = [ self.packages.${pkgs.system}.default ];
      };
    };
}
