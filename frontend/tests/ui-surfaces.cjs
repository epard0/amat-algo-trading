// Fast read-only responsive checks; table-layout.cjs adds all Performance studies.
process.env.AMAT_SKIP_STUDIES = '1';
require('./table-layout.cjs');
