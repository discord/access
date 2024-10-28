import * as React from 'react';
import {Link as RouterLink, Route, Routes} from 'react-router-dom';

import {styled} from '@mui/material/styles';
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
import ListTags from './pages/tags/List';
import ListUsers from './pages/users/List';
import NavItems from './components/NavItems';
import NotFound from './pages/NotFound';
import {ReadApp} from './pages/apps/app_detail';
import ReadGroup from './pages/groups/Read';
import ReadTag from './pages/tags/Read';
import ReadUser from './pages/users/Read';
import {useCurrentUser} from './authentication';
import ReadRequest from './pages/requests/Read';

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

function Dashboard() {
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
            <Typography component="h1" variant="h5" sx={{px: 2}}>
              ACCESS
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
      </Drawer>
      <Box
        component="main"
        sx={{
          backgroundColor: (theme) =>
            theme.palette.mode === 'light' ? theme.palette.grey[100] : theme.palette.grey[900],
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
            <Route path="*" element={<NotFound />} />
          </Routes>
        </Container>
      </Box>
    </Box>
  );
}

export default function App() {
  useCurrentUser();
  return <Dashboard />;
}
