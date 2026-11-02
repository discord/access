// Fails when the root `engines.node` admits a Node version that some locked package
// doesn't support. npm compares a package's `engines` only with the Node running the
// install, so CI passes on the versions it runs while the root range keeps advertising
// versions a dependency has dropped. Run it with `npm run check:engines`.
import {readFileSync} from 'node:fs';
import semver from 'semver';

const rootRange = JSON.parse(readFileSync('package.json', 'utf8')).engines?.node;
if (typeof rootRange !== 'string' || !semver.validRange(rootRange)) {
  console.error(`package.json engines.node is not a valid semver range: ${JSON.stringify(rootRange)}`);
  process.exit(1);
}

const {packages} = JSON.parse(readFileSync('package-lock.json', 'utf8'));
const conflicts = [];
for (const [path, metadata] of Object.entries(packages)) {
  // The empty path is the root package itself.
  const range = path === '' ? undefined : metadata.engines?.node;
  if (range === undefined) continue;
  if (typeof range !== 'string' || !semver.validRange(range)) {
    console.warn(`Skipping ${path}: engines.node is not a valid semver range: ${JSON.stringify(range)}`);
    continue;
  }
  if (!semver.subset(rootRange, range)) conflicts.push(`  ${path}: ${range}`);
}

if (conflicts.length > 0) {
  console.error(
    `package.json engines.node (${rootRange}) admits Node versions these locked packages don't support:\n` +
      `${conflicts.join('\n')}\n` +
      'Narrow engines.node to versions every package supports, then refresh the root metadata in ' +
      'package-lock.json with the npm version CI pins.',
  );
  process.exit(1);
}
console.log(`Every locked package supports engines.node (${rootRange}).`);
