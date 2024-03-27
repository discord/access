import Breadcrumbs from '@mui/material/Breadcrumbs';
import Typography from '@mui/material/Typography';

import Link, {LinkProps} from '@mui/material/Link';
import {Link as RouterLink, useLocation} from 'react-router-dom';

export default function Crumbs() {
  const location = useLocation();
  const pathnames = location.pathname.split('/').filter((x) => x);

  return (
    <Breadcrumbs aria-label="breadcrumb" sx={{mt: 2, ml: 2}}>
      <Link component={RouterLink} underline="hover" color="inherit" to="/">
        Home
      </Link>
      {pathnames.map((value, index) => {
        const last = index === pathnames.length - 1;
        const to = `/${pathnames.slice(0, index + 1).join('/')}`;

        let display = decodeURI(value);
        if (new RegExp('^.*@.*\\..*$').test(display)) {
          display = display.toLowerCase();
        } else {
          display = display[0].toUpperCase() + display.substring(1);
        }

        return last ? (
          <Typography color="text.primary" key={to}>
            {display}
          </Typography>
        ) : (
          <Link component={RouterLink} underline="hover" color="inherit" to={to} key={to}>
            {display}
          </Link>
        );
      })}
    </Breadcrumbs>
  );
}
