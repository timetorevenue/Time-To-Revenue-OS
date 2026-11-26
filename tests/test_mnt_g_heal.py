"""Run the real healer in a fake mount/command environment; never touch host mounts."""
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / 'tools/aos-mnt-g-heal.sh'
MOUNT = '222 82 0:74 / /mnt/g rw,relatime - 9p G: rw,aname=drvfs;path=G:\n'


class MountHealSafetyTests(unittest.TestCase):
    def run_healer(self, mounts=MOUNT, fstab='G: /mnt/g drvfs defaults,nofail 0 0\n',
                   present=True, busy=False, healthy=False, mount_fails=False):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / 'mountinfo').write_text(mounts)
            (root / 'fstab').write_text(fstab)
            script = SCRIPT.read_text().replace('/proc/self/mountinfo', str(root / 'mountinfo'))
            script = script.replace('/etc/fstab', str(root / 'fstab'))
            (root / 'heal').write_text(script)
            # Replace only external commands; shell control flow and awk are real.
            commands = {
                'timeout': '''case "$2" in
ls) [ "$HEALTHY" = 1 ] || [ -f "$TEST_ROOT/restored" ] ;;
*) [ "$PRESENT" = 1 ] && echo present ;;
esac
''',
                'umount': '''echo "umount $*" >> "$TEST_ROOT/actions"
[ "$BUSY" != 1 ] || exit 32
: > "$TEST_ROOT/mountinfo"
''',
                'mount': '''echo "mount $*" >> "$TEST_ROOT/actions"
[ "$MOUNT_FAILS" != 1 ] || exit 32
printf '%s\n' '222 82 0:74 / /mnt/g rw - 9p G: rw' > "$TEST_ROOT/mountinfo"
: > "$TEST_ROOT/restored"
''',
            }
            for name, body in commands.items():
                path = root / name
                path.write_text('#!/bin/sh\n' + body)
                path.chmod(0o700)
            env = dict(os.environ, PATH=f'{root}:/usr/bin:/bin', TEST_ROOT=str(root),
                       PRESENT=str(int(present)), BUSY=str(int(busy)), HEALTHY=str(int(healthy)),
                       MOUNT_FAILS=str(int(mount_fails)))
            result = subprocess.run(['/bin/sh', str(root / 'heal')], env=env,
                                    capture_output=True, text=True, timeout=5)
            actions = (root / 'actions').read_text().splitlines() if (root / 'actions').exists() else []
            return result, actions

    def test_busy_mount_is_never_detached_or_replaced(self):
        result, actions = self.run_healer(busy=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(actions, ['umount /mnt/g'])

    def test_stacked_or_wrong_source_mounts_are_untouched(self):
        for mounts in (MOUNT + MOUNT, MOUNT.replace('9p G:', '9p H:'),
                       MOUNT.replace('9p G:', 'ext4 /dev/sdb')):
            with self.subTest(mounts=mounts):
                result, actions = self.run_healer(mounts=mounts)
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(actions, [])

    def test_unexpected_fstab_is_untouched(self):
        for fstab in ('H: /mnt/g drvfs defaults 0 0\n', '',
                      'G: /mnt/g ext4 defaults 0 0\n'):
            with self.subTest(fstab=fstab):
                result, actions = self.run_healer(fstab=fstab)
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(actions, [])

    def test_absent_backing_drive_is_untouched(self):
        _, actions = self.run_healer(present=False)
        self.assertEqual(actions, [])

    def test_healthy_mount_is_untouched(self):
        result, actions = self.run_healer(healthy=True)
        self.assertEqual(result.returncode, 0)
        self.assertEqual(actions, [])

    def test_recovery_uses_one_ordinary_unmount_and_fstab_mount(self):
        result, actions = self.run_healer()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(actions, ['umount /mnt/g', 'mount /mnt/g'])

    def test_failed_mount_is_reported_without_retry_or_stack(self):
        result, actions = self.run_healer(mount_fails=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(actions, ['umount /mnt/g', 'mount /mnt/g'])

    def test_unmounted_target_uses_existing_fstab(self):
        result, actions = self.run_healer(mounts='')
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(actions, ['mount /mnt/g'])
