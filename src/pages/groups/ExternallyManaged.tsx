import React from 'react';

import {Link as RouterLink} from 'react-router-dom';

import Avatar from '@mui/material/Avatar';
import Box from '@mui/material/Box';
import Button from '@mui/material/Button';
import Dialog from '@mui/material/Dialog';
import DialogActions from '@mui/material/DialogActions';
import DialogContent from '@mui/material/DialogContent';
import DialogTitle from '@mui/material/DialogTitle';
import Link from '@mui/material/Avatar';
import ListItemAvatar from '@mui/material/ListItemAvatar';
import ListItemButton from '@mui/material/ListItemButton';
import ListItemText from '@mui/material/ListItemText';

import ExternalIcon from '@mui/icons-material/Outbound';

import {useGetGroupById} from '../../api/apiComponents';
import {PolymorphicGroup} from '../../api/apiSchemas';

interface ExternallyManagedButtonProps {
  setOpen(open: boolean): any;
}

function ExternallyManagedButton(props: ExternallyManagedButtonProps) {
  return (
    <ListItemButton key="managedexternally" onClick={() => props.setOpen(true)}>
      <ListItemAvatar>
        <Avatar sx={{bgcolor: 'primary.main'}}>
          <ExternalIcon />
        </Avatar>
      </ListItemAvatar>
      <ListItemText primary="Managed Externally" />
    </ListItemButton>
  );
}

interface RuleLinksProps {
  groupId: string;
  index: number;
}

function RuleLinks(props: RuleLinksProps) {
  const {data, isError, isLoading} = useGetGroupById({
    pathParams: {groupId: props.groupId},
  });

  return (
    <>
      {!isError && !isLoading ? (
        <Link
          key={props.groupId}
          to={`/groups/${(data ?? ({} as PolymorphicGroup)).name}`}
          sx={{
            textDecoration: 'none',
            paddingLeft: '4px',
          }}
          component={RouterLink}>
          {(props.index ? ', ' : '') + (data ?? ({} as PolymorphicGroup)).name}
        </Link>
      ) : (
        <>{props.groupId}</>
      )}
    </>
  );
}

interface RuleFormattingProps {
  ruleName: string;
  rule: string;
  groupName: string;
}

function RuleFormatting(props: RuleFormattingProps) {
  const boxStyle = {
    // just for making the if/then pretty
    fontWeight: 'fontWeightMedium',
    bgcolor: 'gainsboro',
    borderRadius: 1,
    padding: '0px 5px 0px 5px',
    margin: '5px 5px 5px 8px',
  };

  const regexp = /(?:isMemberOfAnyGroup|isMemberOfGroup)\(([^\)]+?)\)/g.exec(props.rule);
  var matches = null;

  if (regexp) {
    matches = Array.from(regexp[1].matchAll(/([0-9A-Za-z]+)/g), (m) => m[0]);
  }

  return (
    <Box component="div" sx={{paddingTop: '12px'}} key="{ruleName}">
      <b>Rule: </b>
      {props.ruleName} <br />
      <Box component="div" sx={{paddingLeft: '1.5em', textIndent: '-1.5em'}}>
        <Box component="span" sx={boxStyle}>
          If
        </Box>
        User is a member of
        {matches ? (
          matches.map((gid: string, index: number) => <RuleLinks key={gid} groupId={gid} index={index} />)
        ) : (
          <>{props.rule}</>
        )}
        <br />
      </Box>
      <Box component="span" sx={boxStyle}>
        Then
      </Box>
      Add to group {props.groupName}
    </Box>
  );
}

interface ExternallyManagedDialogProps {
  group: PolymorphicGroup;
  setOpen(open: boolean): any;
}

function ExternallyManagedDialog(props: ExternallyManagedDialogProps) {
  const add_s =
    props.group.externally_managed_data && Object.keys(props.group.externally_managed_data).length > 1 ? 's' : '';
  const title = props.group.name + ' Group Rule' + add_s;

  return (
    <Dialog open fullWidth onClose={() => props.setOpen(false)}>
      <DialogTitle sx={{paddingBottom: '0px'}}>{title}</DialogTitle>
      <DialogContent>
        {props.group.externally_managed_data && Object.keys(props.group.externally_managed_data).length > 0 ? (
          Object.entries(props.group.externally_managed_data).map(([ruleName, rule]: [string, string]) => (
            <RuleFormatting key={ruleName} ruleName={ruleName} rule={rule} groupName={props.group.name} />
          ))
        ) : (
          <>Not managed by Okta group rules</>
        )}
      </DialogContent>
      <DialogActions>
        <Button onClick={() => props.setOpen(false)}>Close</Button>
      </DialogActions>
    </Dialog>
  );
}

interface ExternallyManagedProps {
  group: PolymorphicGroup;
}

export default function ExternallyManaged(props: ExternallyManagedProps) {
  const [open, setOpen] = React.useState(false);

  return (
    <>
      <ExternallyManagedButton setOpen={setOpen} />
      {open ? <ExternallyManagedDialog group={props.group} setOpen={setOpen} /> : null}
    </>
  );
}
