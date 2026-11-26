#!/bin/sh
# Heal the existing fstab drvfs mount of Windows G: (Google Drive for Desktop) at /mnt/g.
#
# When Drive for Desktop restarts it recreates G:, and the WSL drvfs mount keeps "mounted"
# while every access returns "No such device". This checks real usability: exactly one mount
# at /mnt/g in the kernel mount table (a stale mount cannot be stat'ed, so `mountpoint`
# misreports it) and a bounded directory listing. A usable mount is never touched. Only when
# it is unusable AND Windows confirms G: exists does it try an ordinary unmount of
# the single verified G: mount and mount from the existing fstab entry. Busy, stacked
# or unexpected mounts are left unchanged. No forced or lazy unmount is permitted.
# Runs as root from aos-mnt-g-heal.timer (installed copy: /usr/local/sbin/aos-mnt-g-heal).
#
# Revisit: when the /etc/fstab G: entry, the Drive letter or the Memory Exchange transport
# changes. Last touched: 2026-11-19.

set -u
MP=${AOS_HEAL_MOUNTPOINT:-/mnt/g}
DRIVE=${AOS_HEAL_DRIVE:-G}
CMD=/mnt/c/Windows/System32/cmd.exe

# Read the kernel's table; never stat the mount point, which fails on a stale mount.
mounts() {
    awk -v mp="$MP" '$5 == mp { n++ } END { print n + 0 }' /proc/self/mountinfo
}

usable() {
    [ "$(mounts)" -eq 1 ] && timeout 30 ls "$MP" >/dev/null 2>&1
}

usable && exit 0

# Some WSL sessions have no binfmt WSLInterop registration. /init is the
# existing Windows executable interpreter; use it without changing host config.
windows_cmd() {
    if [ -r /proc/sys/fs/binfmt_misc/WSLInterop ]; then
        timeout 30 "$CMD" "$@"
    else
        timeout 30 /init "$CMD" "$@"
    fi
}
present=$(cd /mnt/c && windows_cmd /d /c "if exist ${DRIVE}:\\ (echo present)" 2>/dev/null | tr -d '\r ')
if [ "$present" != "present" ]; then
    echo "$MP unusable; Windows ${DRIVE}: is not available (or Windows could not be asked); left unchanged"
    exit 0
fi

layers=$(mounts)
[ "$layers" -le 1 ] || { echo "$MP has stacked mounts; left unchanged"; exit 1; }
# Do not detach another filesystem, or replace it using an unexpected fstab entry.
awk -v mp="$MP" -v source="${DRIVE}:" '
    $2 == mp && $1 !~ /^#/ { n++; if ($1 == source && $3 == "drvfs") ok++ }
    END { exit !(n == 1 && ok == 1) }
' /etc/fstab || { echo "$MP fstab source/type is unexpected; left unchanged"; exit 1; }
if [ "$layers" -eq 1 ]; then
    awk -v mp="$MP" -v source="${DRIVE}:" '
        $5 == mp {
            for (i = 7; i <= NF; i++) {
                if ($i == "-") { ok = ($(i+1) == "9p" && $(i+2) == source); break }
            }
        }
        END { exit !ok }
    ' /proc/self/mountinfo || { echo "$MP mounted source/type is unexpected; left unchanged"; exit 1; }
    # The kernel refuses an ordinary unmount with active references (EBUSY).
    # Stop on failure; never detach a busy mount or stack a replacement over it.
    umount "$MP" || { echo "umount $MP refused; left unchanged"; exit 1; }
fi
echo "$MP: mounting verified Windows ${DRIVE}: from fstab"
mount "$MP" || { echo "mount $MP failed"; exit 1; }

if usable; then
    echo "$MP restored"
    exit 0
fi
echo "$MP still unusable after remount"
exit 1
