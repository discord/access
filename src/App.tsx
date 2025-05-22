import * as React from 'react';
import {Link as RouterLink, Route, Routes} from 'react-router-dom';

import {createTheme, styled, ThemeProvider} from '@mui/material/styles';
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

const drawerWidth: number = 240;
const darkBg1: string = '#181818';
const darkBg2: string = '#242424';
const darkBg3: string = '#080808';
const darkModeText: string = '#DDDDDD';

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
    <Box sx={{display: 'flex'}}>
      <AppBar position="absolute" open={open}>
        <Toolbar
          sx={{
            pr: '24px', // keep right padding when drawer closed
            backgroundColor: (theme) => (theme.palette.mode === 'light' ? theme.palette.primary.main : '#2A2C4F'),
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
      <Drawer
        sx={{backgroundColor: (theme) => (theme.palette.mode === 'light' ? 'default' : darkBg2)}}
        variant="permanent"
        open={open}>
        <Toolbar
          sx={{
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'flex-end',
            px: [1],
            backgroundColor: (theme) => (theme.palette.mode === 'light' ? 'default' : darkBg2),
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
            {useTheme().palette.mode === 'light' ? (
              <Avatar src="/logo-square.png" variant="square" />
            ) : (
              <Avatar src="/logo-square-dark.png" variant="square" />
            )}
            <Typography component="h1" variant="h5" sx={{px: 2}} color="text.accent">
              ACCESS
            </Typography>
          </Link>
          <IconButton onClick={toggleDrawer}>
            <ChevronLeftIcon />
          </IconButton>
        </Toolbar>
        <Divider />
        <List sx={{backgroundColor: (theme) => (theme.palette.mode === 'light' ? 'default' : darkBg2)}} component="nav">
          <NavItems open={open} />
        </List>
        <Stack
          sx={{backgroundColor: (theme) => (theme.palette.mode === 'light' ? 'default' : darkBg2)}}
          marginTop="auto"
          p={2}>
          <ThemeToggle setThemeMode={setThemeMode} condensed={!open} />
        </Stack>
      </Drawer>
      <Box
        component="main"
        sx={{
          backgroundColor: (theme) => (theme.palette.mode === 'light' ? theme.palette.grey[200] : '#1E1E1E'),
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

export default function App() {
  const storedTheme = localStorage.getItem('user-set-color-scheme') as 'light' | 'dark' | null;
  const systemTheme = useMediaQuery('(prefers-color-scheme: dark)') ? 'dark' : 'light';
  const initialMode = storedTheme ?? systemTheme;
  const [mode, setMode] = React.useState<PaletteMode>(initialMode);

  // See https://discord.com/branding
  let theme = React.useMemo(() => {
    const base = createTheme({
      palette: {
        mode,
        primary: {
          main: mode === 'light' ? '#5865F2' : '#2A2C4F',
          light: '#A5B2FF',
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
        success: {
          main: '#57F287',
        },
        text: {
          accent: mode === 'light' ? '#5865F2' : '#646cbd',
          primary: mode === 'light' ? grey[900] : darkModeText,
        },
        background: {
          paper: mode === 'light' ? '#FFFFFF' : darkBg1,
          default: mode === 'light' ? '#FFFFFF' : darkBg1,
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
        MuiButtonBase: {
          styleOverrides: {
            root: {
              color: mode === 'light' ? 'default' : darkModeText,
            },
          },
        },
        MuiButton: {
          styleOverrides: {
            root: {
              color: mode === 'light' ? 'default' : darkModeText,
            },
          },
        },
        MuiSvgIcon: {
          styleOverrides: {
            root: {
              color: mode === 'light' ? 'default' : darkModeText,
            },
          },
        },
        MuiDrawer: {
          styleOverrides: {
            paper: {
              backgroundColor: mode === 'light' ? 'default' : darkBg2,
            },
          },
        },
        MuiDialog: {
          styleOverrides: {
            paper: {
              backgroundColor: mode === 'light' ? 'default' : darkBg3,
            },
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
            color: {main: mode === 'light' ? yellow[100] : alpha(yellow[500], 0.3)},
            name: 'warning',
          }),
          danger: base.palette.augmentColor({
            color: {main: mode === 'light' ? red[100] : alpha(red[500], 0.3)},
            name: 'danger',
          }),
        },
      },
    });
  }, [mode]);

  useCurrentUser();
  return (
    <ThemeProvider theme={theme}>
      <CssBaseline />
      <Dashboard setThemeMode={setMode} />
    </ThemeProvider>
  );
}
