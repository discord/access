import * as React from 'react';
import {Link as RouterLink, Route, Routes, useSearchParams} from 'react-router-dom';

import {createTheme, styled, Theme, ThemeProvider} from '@mui/material/styles';
import Link from '@mui/material/Link';
import MuiDrawer from '@mui/material/Drawer';
import Box from '@mui/material/Box';
import MuiAppBar, {AppBarProps as MuiAppBarProps} from '@mui/material/AppBar';
import Toolbar from '@mui/material/Toolbar';
import List from '@mui/material/List';
import Typography from '@mui/material/Typography';
import Divider from '@mui/material/Divider';
import Avatar from '@mui/material/Avatar';
import IconButton from '@mui/material/IconButton';
import Container from '@mui/material/Container';
import MenuIcon from '@mui/icons-material/Menu';
import ChevronLeftIcon from '@mui/icons-material/ChevronLeft';
import AccountIcon from '@mui/icons-material/AccountCircle';
import {
  alpha,
  CssBaseline,
  PaletteMode,
  Stack,
  ToggleButton,
  ToggleButtonGroup,
  Tooltip,
  useMediaQuery,
  useTheme,
} from '@mui/material';
import {DarkMode, LightMode, Monitor} from '@mui/icons-material';
import {lightGreen, red, yellow, grey} from '@mui/material/colors';
import * as Sentry from '@sentry/react';

import AuditGroup from './pages/groups/Audit';
import AuditRole from './pages/roles/Audit';
import AuditUser from './pages/users/Audit';
import ExpiringGroups from './pages/groups/Expiring';
import ExpiringRoles from './pages/roles/Expiring';
import Home from './pages/Home';
import ListApps from './pages/apps/List';
import ListGroups from './pages/groups/List';
import ListRequests from './pages/requests/List';
import ListRoles from './pages/roles/List';
import ListRoleRequests from './pages/role_requests/List';
import ListTags from './pages/tags/List';
import ListUsers from './pages/users/List';
import NavItems from './components/NavItems';
import NotFound from './pages/NotFound';
import ReadApp from './pages/apps/Read';
import ReadGroup from './pages/groups/Read';
import ReadTag from './pages/tags/Read';
import ReadUser from './pages/users/Read';
import {useCurrentUser} from './authentication';
import ReadRequest from './pages/requests/Read';
import ReadRoleRequest from './pages/role_requests/Read';

import {appName} from './config/accessConfig';

const drawerWidth: number = 240;

interface AppBarProps extends MuiAppBarProps {
  open?: boolean;
}

const AppBar = styled(MuiAppBar, {
  shouldForwardProp: (prop) => prop !== 'open',
})<AppBarProps>(({theme, open}) => ({
  zIndex: theme.zIndex.drawer + 1,
  transition: theme.transitions.create(['width', 'margin'], {
    easing: theme.transitions.easing.sharp,
    duration: theme.transitions.duration.leavingScreen,
  }),
  ...(open && {
    marginLeft: drawerWidth,
    width: `calc(100% - ${drawerWidth}px)`,
    transition: theme.transitions.create(['width', 'margin'], {
      easing: theme.transitions.easing.sharp,
      duration: theme.transitions.duration.enteringScreen,
    }),
  }),
}));

const Drawer = styled(MuiDrawer, {
  shouldForwardProp: (prop) => prop !== 'open',
})(({theme, open}) => ({
  '& .MuiDrawer-paper': {
    position: 'relative',
    whiteSpace: 'nowrap',
    width: drawerWidth,
    transition: theme.transitions.create('width', {
      easing: theme.transitions.easing.sharp,
      duration: theme.transitions.duration.enteringScreen,
    }),
    boxSizing: 'border-box',
    ...(!open && {
      overflowX: 'hidden',
      transition: theme.transitions.create('width', {
        easing: theme.transitions.easing.sharp,
        duration: theme.transitions.duration.leavingScreen,
      }),
      width: theme.spacing(7),
      [theme.breakpoints.up('sm')]: {
        width: theme.spacing(9),
      },
    }),
  },
}));

function ThemeToggle({setThemeMode, condensed}: {setThemeMode: (theme: PaletteMode) => void; condensed: boolean}) {
  const [storedTheme, setStoredTheme] = React.useState(
    localStorage.getItem('user-set-color-scheme') as 'light' | 'dark' | null,
  );
  const currentTheme = useTheme();
  const systemTheme = useMediaQuery('(prefers-color-scheme: dark)') ? 'dark' : 'light';

  const handleThemeOverride = (theme: PaletteMode) => {
    setThemeMode(theme);
    localStorage.setItem('user-set-color-scheme', theme);
    setStoredTheme(theme);
  };

  const handleSystemDefault = () => {
    setThemeMode(systemTheme);
    localStorage.removeItem('user-set-color-scheme');
    setStoredTheme(null);
  };

  return (
    <ToggleButtonGroup size="small">
      {(currentTheme.palette.mode != 'light' || !condensed) && (
        <Tooltip title="Light Mode">
          <ToggleButton
            value="left"
            selected={storedTheme === 'light'}
            onClick={() => handleThemeOverride('light')}
            aria-label="Light mode">
            <LightMode />
          </ToggleButton>
        </Tooltip>
      )}
      {!condensed && (
        <Tooltip title="System Default">
          <ToggleButton
            value="center"
            selected={storedTheme == null}
            onClick={handleSystemDefault}
            aria-label="System Default">
            <Monitor />
          </ToggleButton>
        </Tooltip>
      )}
      {(currentTheme.palette.mode != 'dark' || !condensed) && (
        <Tooltip title="Dark Mode">
          <ToggleButton
            value="right"
            selected={storedTheme === 'dark'}
            onClick={() => handleThemeOverride('dark')}
            aria-label="Dark mode">
            <DarkMode />
          </ToggleButton>
        </Tooltip>
      )}
    </ToggleButtonGroup>
  );
}

function Dashboard({setThemeMode}: {setThemeMode: (theme: PaletteMode) => void}) {
  const [open, setOpen] = React.useState(true);
  const toggleDrawer = () => {
    setOpen(!open);
  };

  return (
    <Box sx={{display: 'flex', minWidth: '20rem', overflowX: 'hidden'}}>
      <AppBar position="absolute" open={open}>
        <Toolbar
          sx={{
            pr: '24px', // keep right padding when drawer closed
          }}>
          <IconButton
            edge="start"
            color="inherit"
            aria-label="open drawer"
            onClick={toggleDrawer}
            sx={{
              marginRight: '36px',
              ...(open && {display: 'none'}),
            }}>
            <MenuIcon />
          </IconButton>
          <Box sx={{flexGrow: 1}} />
          <IconButton color="inherit" component={RouterLink} to="/users/@me">
            <AccountIcon />
          </IconButton>
        </Toolbar>
      </AppBar>
      <Drawer variant="permanent" open={open}>
        <Toolbar
          sx={{
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'flex-end',
            px: [1],
          }}>
          <Link
            to="/"
            component={RouterLink}
            sx={{
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'flex-start',
              px: 2,
              textDecoration: 'none',
            }}>
            <Avatar src="/logo-square.png" variant="square" />
            <Typography
              title={appName}
              component="h1"
              variant="h5"
              noWrap
              sx={{px: 2, maxWidth: 125, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap'}}
              color="text.accent">
              {appName.toUpperCase()}
            </Typography>
          </Link>
          <IconButton onClick={toggleDrawer}>
            <ChevronLeftIcon />
          </IconButton>
        </Toolbar>
        <Divider />
        <List component="nav">
          <NavItems open={open} />
        </List>
        <Stack marginTop="auto" p={2}>
          <ThemeToggle setThemeMode={setThemeMode} condensed={!open} />
        </Stack>
      </Drawer>
      <Box
        component="main"
        sx={{
          backgroundColor: (theme) =>
            theme.palette.mode === 'light' ? theme.palette.grey[200] : theme.palette.grey[800],
          flexGrow: 1,
          height: '100vh',
          overflow: 'auto',
        }}>
        <Toolbar />
        <Container maxWidth="xl" sx={{mt: 4, mb: 4}}>
          <Routes>
            <Route path="/" element={<Home />} />
            <Route path="/users" element={<ListUsers />} />
            <Route path="/users/:id" element={<ReadUser />} />
            <Route path="/users/:id/audit" element={<AuditUser />} />
            <Route path="/groups" element={<ListGroups />} />
            <Route path="/groups/:id" element={<ReadGroup />} />
            <Route path="/groups/:id/audit" element={<AuditGroup />} />
            <Route path="/roles" element={<ListRoles />} />
            <Route path="/roles/:id" element={<ReadGroup />} />
            <Route path="/roles/:id/audit" element={<AuditRole />} />
            <Route path="/apps" element={<ListApps />} />
            <Route path="/apps/:id" element={<ReadApp />} />
            <Route path="/requests" element={<ListRequests />} />
            <Route path="/requests/:id" element={<ReadRequest />} />
            <Route path="/expiring-groups" element={<ExpiringGroups />} />
            <Route path="/expiring-roles" element={<ExpiringRoles />} />
            <Route path="/tags" element={<ListTags />} />
            <Route path="/tags/:id" element={<ReadTag />} />
            <Route path="/role-requests" element={<ListRoleRequests />} />
            <Route path="/role-requests/:id" element={<ReadRoleRequest />} />
            <Route path="*" element={<NotFound />} />
          </Routes>
        </Container>
      </Box>
    </Box>
  );
}

interface AppStateProps {
  source: string | undefined;
  theme: Theme;
  setMode: (mode: PaletteMode) => void;
}

export default function App() {
  const storedTheme = localStorage.getItem('user-set-color-scheme') as 'light' | 'dark' | null;
  const systemTheme = useMediaQuery('(prefers-color-scheme: dark)') ? 'dark' : 'light';
  const initialMode = storedTheme ?? systemTheme;
  const [mode, setMode] = React.useState<PaletteMode>(initialMode);
  const [searchParams, setSearchParams] = useSearchParams();
  const source = searchParams.get('source') ?? undefined;

  // See https://discord.com/branding
  let theme = React.useMemo(() => {
    const base = createTheme({
      palette: {
        mode,
        primary: {
          main: '#5865F2',
          light: '#A5B2FF',
          dark: '#5C6299',
        },
        secondary: {
          main: '#EB459E',
        },
        error: {
          main: '#ED4245',
        },
        warning: {
          main: '#FEE75C',
        },
        info: {
          main: '#4287f5',
        },
        success: {
          main: '#57F287',
        },
        text: {
          accent: mode === 'light' ? '#5865F2' : '#A5B2FF',
        },
      },
      components: {
        MuiChip: {
          styleOverrides: {
            colorPrimary: ({ownerState, theme}) => ({
              ...(ownerState.variant === 'outlined' &&
                ownerState.color === 'primary' && {
                  color: theme.palette.text.accent,
                  borderColor: theme.palette.text.accent,
                }),
            }),
            deleteIcon: ({ownerState, theme}) => ({
              ...(ownerState.variant === 'outlined' &&
                ownerState.color === 'primary' && {
                  color: theme.palette.text.accent,
                }),
            }),
          },
        },
      },
    });
    return createTheme(base, {
      palette: {
        highlight: {
          success: base.palette.augmentColor({
            color: {main: mode === 'light' ? lightGreen[100] : alpha(lightGreen[500], 0.3)},
            name: 'success',
          }),
          warning: base.palette.augmentColor({
            color: {
              main: mode === 'light' ? yellow[100] : alpha(yellow[500], 0.3),
              // using this as a general contrast color but MUI doesn't have that field built in
              contrastText: mode === 'light' ? alpha(yellow[300], 0.8) : alpha(yellow[200], 0.3),
            },
            name: 'warning',
          }),
          danger: base.palette.augmentColor({
            color: {
              main: mode === 'light' ? red[100] : alpha(red[500], 0.3),
              // using this as a general contrast color but MUI doesn't have that field built in
              contrastText: mode === 'light' ? alpha(red[200], 0.7) : alpha(red[300], 0.3),
            },
            name: 'danger',
          }),
          info: base.palette.augmentColor({
            color: {
              main: mode === 'light' ? grey[100] : alpha(grey[700], 0.3),
              // using this as a general contrast color but MUI doesn't have that field built in
              contrastText: mode === 'light' ? alpha(grey[300], 0.8) : alpha(grey[200], 0.3),
            },
            name: 'info',
          }),
        },
      },
    });
  }, [mode]);

  const updateMode = React.useCallback(
    (mode: PaletteMode) => {
      setMode(mode);
      localStorage.setItem('user-set-color-scheme', mode);
    },
    [setMode],
  );

  return <AppState source={source} theme={theme} setMode={updateMode} />;
}

function AppState({source, theme, setMode}: AppStateProps) {
  useCurrentUser();

  React.useEffect(() => {
    if (source) {
      Sentry.addBreadcrumb({
        category: 'navigation',
        message: `Access navigation from source referrer`,
        data: {source},
        level: 'info',
      });
      Sentry.setTag('source', source);
    }
  }, [source]);

  return (
    <ThemeProvider theme={theme}>
      <CssBaseline />
      <Dashboard setThemeMode={setMode} />
    </ThemeProvider>
  );
}
