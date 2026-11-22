"""Build the fixed verifier image from pinned public runtime and dependency artifacts."""

import argparse
from hashlib import file_digest
from pathlib import Path
import shutil
import subprocess
import tarfile
from tempfile import TemporaryDirectory


SOURCE = Path(__file__).resolve().parent
BASE = 'python@sha256:516b51cb9db2ef061e465c803e50f64361b82cefd91e525b889b967b83e83527'
IMAGE = 'harness-order-check:17.0.20-6.1.14'
ADOPTIUM = 'https://github.com/adoptium/temurin17-binaries/releases/download/jdk-17.0.20%2B8/'
MAVEN = 'https://repo.maven.apache.org/maven2/'
JDK_HASHES = {
    'aarch64': 'd143936f473a4cb24e3b0e247d6d0775769d55ec9775c339540e753059a8d77a',
    'x64': 'be7668bc030d578b83d6d5ef9221d6d6729bbbca8cf94a7d52e16ac68b5a5a35',
}


def check_digest(path: Path, digest: str) -> None:
    with path.open('rb') as handle:
        actual = file_digest(handle, 'sha256').hexdigest()
    if actual != digest:
        raise ValueError(f'Digest mismatch for {path.name}')


def fetch(url: str, output: Path) -> None:
    subprocess.run(['curl', '-fsSL', '--retry', '2', '--max-time', '240', '-o', str(output), url],
                   check=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--jdk-archive', type=Path, help='Use an already downloaded, checksum-verified archive')
    args = parser.parse_args()
    architecture = subprocess.check_output(['docker', 'info', '--format', '{{.Architecture}}'], text=True).strip()
    arch = {'arm64': 'aarch64', 'aarch64': 'aarch64', 'amd64': 'x64', 'x86_64': 'x64'}[architecture]
    subprocess.run(['docker', 'image', 'inspect', BASE], check=True, stdout=subprocess.DEVNULL)
    with TemporaryDirectory(prefix='order-verifier-build-') as dirname:
        context = Path(dirname)
        (context / 'jars').mkdir()
        archive = context / f'OpenJDK17U-jdk_{arch}_linux_hotspot_17.0.20_8.tar.gz'
        if args.jdk_archive:
            archive = args.jdk_archive.resolve(strict=True)
        else:
            fetch(ADOPTIUM + archive.name, archive)
        check_digest(archive, JDK_HASHES[arch])
        with tarfile.open(archive, 'r:gz') as bundle:
            bundle.extractall(context, filter='data')
        (context / 'jdk-17.0.20+8').rename(context / 'jdk')
        for line in (SOURCE / 'jars.sha256').read_text().splitlines():
            digest, path = line.split()
            cached = Path.home() / '.m2/repository' / path
            target = context / 'jars' / Path(path).name
            if cached.is_file():
                shutil.copyfile(cached, target)
            else:
                fetch(MAVEN + path, target)
            check_digest(target, digest)
        for name in ('ContractCheck.java', 'check.sh', 'Dockerfile'):
            shutil.copyfile(SOURCE / name, context / name)
        (context / '.dockerignore').write_text('*.tar.gz\n')
        subprocess.run(['docker', 'build', '--network=none', '--pull=false', '-t', IMAGE, str(context)],
                       check=True)


if __name__ == '__main__':
    main()
