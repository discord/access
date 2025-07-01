import {createSvgIcon} from '@mui/material';

/** A version of the MUI `MoreTime` icon that's centered on the circle (instead of on the bounding box of all the elements) */
const MoreTime = createSvgIcon(
  <svg
    xmlns="http://www.w3.org/2000/svg"
    enable-background="new 0 0 24 24"
    height="24px"
    viewBox="0 0 24 24"
    width="24px">
    <g>
      <rect fill="none" height="24" width="24" />
    </g>
    <g>
      <g>
        <polygon fill="currentColor" points="15.7,15.9 16.5,14.7 12.5,12.3 12.5,7 11,7 11,13" />
        <path
          fill="currentColor"
          d="m 18.92,11 c 0.05,0.33 0.08,0.66 0.08,1 0,3.9 -3.1,7 -7,7 -3.9,0 -7,-3.1 -7,-7 0,-3.9 3.1,-7 7,-7 0.7,0 1.37,0.1 2,0.29 V 3.23 C 13.36,3.08 12.69,3 12,3 7,3 3,7 3,12 c 0,5 4,9 9,9 5,0 9,-4 9,-9 0,-0.34 -0.02,-0.67 -0.06,-1 z"
        />
        <polygon fill="currentColor" points="19,1 19,4 16,4 16,6 19,6 19,9 21,9 21,6 24,6 24,4 21,4 21,1" />
      </g>
    </g>
  </svg>,
  'MoreTime',
);

export default MoreTime;
