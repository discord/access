import * as React from 'react';
import {Link as RouterLink} from 'react-router-dom';
import Divider from '@mui/material/Divider';
import List from '@mui/material/List';
import ListItem, {ListItemProps} from '@mui/material/ListItem';
import ListItemButton from '@mui/material/ListItemButton';
import Collapse from '@mui/material/Collapse';
import ListItemIcon from '@mui/material/ListItemIcon';
import ListItemText from '@mui/material/ListItemText';
import ListSubheader from '@mui/material/ListSubheader';
import ExpandLess from '@mui/icons-material/ExpandLess';
import ExpandMore from '@mui/icons-material/ExpandMore';
import UserIcon from '@mui/icons-material/Person';
import GroupIcon from '@mui/icons-material/People';
import RoleIcon from '@mui/icons-material/Diversity3';
import AppIcon from '@mui/icons-material/AppShortcut';
import AccessRequestIcon from './icons/MoreTime';
import RequestFromMe from '@mui/icons-material/AssignmentInd';
import RequestToMe from '@mui/icons-material/AssignmentReturn';
import RequestAll from '@mui/icons-material/Assignment';
import ExpiringGroupsIcon from '@mui/icons-material/RunningWithErrors';
import ExpiringRolesIcon from '@mui/icons-material/HeartBroken';
import ExpiringMyAccess from '@mui/icons-material/AccountBox';
import ExpiringOwnedByMe from '@mui/icons-material/AccountTree';
import ExpiringRolesOwnedByMe from '@mui/icons-material/HowToReg';
import ExpiringAll from '@mui/icons-material/SwitchAccount';
import RoleRequestIcon from '@mui/icons-material/WorkHistory';
import GroupRequestIcon from '@mui/icons-material/GroupAdd';

interface ListItemLinkProps extends ListItemProps {
  to: string;
  displayText: string;
  displayIcon?: JSX.Element;
  open?: boolean;
}

function ListItemLink(props: ListItemLinkProps) {
  const {to, displayText, displayIcon, open, ...other} = props;

  let icon = null;
  if (open != null) {
    icon = open ? <ExpandLess /> : <ExpandMore />;
  }

  other.sx = Object.assign({textDecoration: 'none', color: 'inherit', p: 0}, other.sx ?? {});

  return (
    <ListItem component={RouterLink as any} to={to} {...other}>
      <ListItemButton>
        <ListItemIcon>{displayIcon}</ListItemIcon>
        <ListItemText primary={displayText} />
        {icon}
      </ListItemButton>
    </ListItem>
  );
}

const subheaderSx = {
  pl: 4,
  lineHeight: '2rem',
  fontSize: '0.7rem',
  fontWeight: 600,
  letterSpacing: '0.08em',
  textTransform: 'uppercase',
  color: 'text.secondary',
};

interface NavItemsProps {
  open: boolean;
}

// Integration MUI Link with React Router Link
// https://mui.com/material-ui/guides/routing/#link
export default function NavItems(props: NavItemsProps) {
  const [openAccessRequests, setOpenAccessRequests] = React.useState(false);
  const [openGroupRequests, setOpenGroupRequests] = React.useState(false);
  const [openExpiringAccess, setOpenExpiringAccess] = React.useState(false);

  return (
    <List>
      <ListItemLink to="/users" displayText="Users" displayIcon={<UserIcon />} />
      <ListItemLink to="/groups" displayText="Groups" displayIcon={<GroupIcon />} />
      <ListItemLink to="/roles" displayText="Roles" displayIcon={<RoleIcon />} />
      <ListItemLink to="/apps" displayText="Apps" displayIcon={<AppIcon />} sx={{pb: 1}} />
      <Divider />
      <ListItemLink
        to="/requests"
        displayText="Access Requests"
        displayIcon={<AccessRequestIcon />}
        open={openAccessRequests}
        onClick={() => setOpenAccessRequests(!openAccessRequests)}
        sx={{pt: 1}}
      />
      <Collapse component="li" in={props.open && openAccessRequests} timeout="auto" unmountOnExit>
        <List disablePadding>
          <ListSubheader disableSticky sx={subheaderSx}>
            Individual
          </ListSubheader>
          <ListItemLink
            to="/requests?requester_user_id=@me"
            displayText="From Me"
            displayIcon={<RequestFromMe />}
            sx={{pl: 4}}
          />
          <ListItemLink
            to="/requests?assignee_user_id=@me"
            displayText="Assigned to Me"
            displayIcon={<RequestToMe />}
            sx={{pl: 4}}
          />
          <ListItemLink to="/requests" displayText="All" displayIcon={<RequestAll />} sx={{pl: 4}} />
          <ListSubheader disableSticky sx={subheaderSx}>
            Role-Based
          </ListSubheader>
          <ListItemLink
            to="/role-requests?requester_user_id=@me"
            displayText="From Me"
            displayIcon={<RequestFromMe />}
            sx={{pl: 4}}
          />
          <ListItemLink
            to="/role-requests?assignee_user_id=@me"
            displayText="Assigned to Me"
            displayIcon={<RequestToMe />}
            sx={{pl: 4}}
          />
          <ListItemLink to="/role-requests" displayText="All" displayIcon={<RequestAll />} sx={{pl: 4}} />
        </List>
      </Collapse>
      <ListItemLink
        to="/group-requests"
        displayText="Group Requests"
        displayIcon={<GroupRequestIcon />}
        open={openGroupRequests}
        onClick={() => setOpenGroupRequests(!openGroupRequests)}
        sx={{py: 1}}
      />
      <Collapse component="li" in={props.open && openGroupRequests} timeout="auto" unmountOnExit>
        <List disablePadding>
          <ListItemLink
            to="/group-requests?requester_user_id=@me"
            displayText="From Me"
            displayIcon={<RequestFromMe />}
            sx={{pl: 4}}
          />
          <ListItemLink
            to="/group-requests?assignee_user_id=@me"
            displayText="Assigned to Me"
            displayIcon={<RequestToMe />}
            sx={{pl: 4}}
          />
          <ListItemLink to="/group-requests" displayText="All" displayIcon={<RequestAll />} sx={{pl: 4}} />
        </List>
      </Collapse>
      <Divider />
      <ListItemLink
        to="/expiring-groups"
        displayText="Expiring Access"
        displayIcon={<ExpiringGroupsIcon />}
        open={openExpiringAccess}
        onClick={() => setOpenExpiringAccess(!openExpiringAccess)}
        sx={{pt: 1}}
      />
      <Collapse component="li" in={props.open && openExpiringAccess} timeout="auto" unmountOnExit>
        <List disablePadding>
          <ListSubheader disableSticky sx={subheaderSx}>
            Individual
          </ListSubheader>
          <ListItemLink
            to="/expiring-groups?user_id=@me"
            displayText="My Access"
            displayIcon={<ExpiringMyAccess />}
            sx={{pl: 4}}
          />
          <ListItemLink
            to="/expiring-groups?owner_id=@me"
            displayText="Owned by Me"
            displayIcon={<ExpiringOwnedByMe />}
            sx={{pl: 4}}
          />
          <ListItemLink to="/expiring-groups" displayText="All" displayIcon={<ExpiringAll />} sx={{pl: 4}} />
          <ListSubheader disableSticky sx={subheaderSx}>
            Role-Based
          </ListSubheader>
          <ListItemLink
            to="/expiring-roles?owner_id=@me"
            displayText="Owned Groups"
            displayIcon={<ExpiringOwnedByMe />}
            sx={{pl: 4}}
          />
          <ListItemLink
            to="/expiring-roles?role_owner_id=@me"
            displayText="Owned Roles"
            displayIcon={<ExpiringRolesOwnedByMe />}
            sx={{pl: 4}}
          />
          <ListItemLink to="/expiring-roles" displayText="All" displayIcon={<ExpiringAll />} sx={{pl: 4}} />
        </List>
      </Collapse>
    </List>
  );
}
