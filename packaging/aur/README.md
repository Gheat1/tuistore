# AUR packaging

Live at [aur.archlinux.org/packages/tuistore](https://aur.archlinux.org/packages/tuistore) — `yay -S tuistore` / `paru -S tuistore`.

`PKGBUILD` here is kept as the source of truth for the `tuistore` AUR
package. It's not auto-published — updates are a `git push` to a
separate, AUR-hosted repo tied to a maintainer's own AUR account
(`ssh://aur@aur.archlinux.org/tuistore.git`), so updating the live AUR
package after a change here is a manual step:

```sh
cd packaging/aur
makepkg --printsrcinfo > .SRCINFO   # regenerate after any PKGBUILD change
git clone ssh://aur@aur.archlinux.org/tuistore.git /tmp/tuistore-aur-push
cp PKGBUILD .SRCINFO /tmp/tuistore-aur-push/
cd /tmp/tuistore-aur-push
git add PKGBUILD .SRCINFO
git commit -m "Update to vX.Y.Z"
git push
```

Same pattern for a version bump: update `pkgver` and `sha256sums` (the
sha256 of `https://github.com/Gheat1/tuistore/archive/refs/tags/vX.Y.Z.tar.gz`),
then repeat the steps above.

Like the [Homebrew tap](https://github.com/Gheat1/homebrew-tuistore), this
installs tuistore + [ricekit](https://github.com/Gheat1/ricekit) into a
private venv via `uv`, since neither is packaged for Arch.
