import {Accordion, AccordionDetails, AccordionSummary} from '@mui/material';
import {AppGroup, OktaUserGroupMember} from '../api/apiSchemas';

interface AccordionListGroupProps {
  group_name: string;
  owner_group: AppGroup;
  member_group: AppGroup;
}

export const AccordionListGroup: React.FC<AccordionListGroupProps> = ({group_name, owner_group, member_group}) => {
  return null;
};
/*
    {app.active_owner_app_groups?.map((appGroup) => (
        <React.Fragment key={appGroup.id}>
          <Grid item xs={6} key={appGroup.id + 'owners'}>

          <Accordion expanded={expanded === 'app-owners'} onChange={handleChange('app-owners')}>
            <AccordionSummary expandIcon={<ExpandMoreIcon />}>
              <Table>
              <TableRow>
                    <TableCell colSpan={2}>
                      <Stack direction="column" spacing={1}>
                        <Typography variant="h6" color="primary">
                          <Link
                            to={`/groups/${appGroup.name}`}
                            sx={{
                              textDecoration: 'none',
                              color: 'inherit',
                            }}
                            component={RouterLink}>
                            App Owners
                          </Link>
                        </Typography>
                        <Typography variant="body1" color="grey">
                          Can manage app and implicitly own all app groups
                        </Typography>
                      </Stack>
                    </TableCell>
                    <TableCell>
                      <Box
                        sx={{
                          display: 'flex',
                          justifyContent: 'flex-end',
                          alignItems: 'right',
                        }}>
                        <Divider sx={{mx: 2}} orientation="vertical" flexItem />
                        Total Owners: {Object.keys(groupMemberships(appGroup.active_user_ownerships)).length}
                      </Box>
                    </TableCell>
                  </TableRow>
                  </Table>
            </AccordionSummary>
            <AccordionDetails>
            <TableContainer component={Paper}>

              <Table sx={{minWidth: 325}} size="small" aria-label="app owners">

                <TableHead>

                  <TableRow>
                    <TableCell>Name</TableCell>
                    <TableCell>Email</TableCell>
                    <TableCell>Ending</TableCell>
                  </TableRow>
                </TableHead>
                <TableBody>
                  {Object.keys(groupMemberships(appGroup.active_user_ownerships)).length > 0 ? (
                    Object.entries(groupMemberships(appGroup.active_user_ownerships))
                      .sort(sortGroupMembers)
                      .map(([userId, users]: [string, Array<OktaUserGroupMember>]) => (
                        <TableRow key={userId}>
                          <TableCell>
                            <Link
                              to={`/users/${users[0].active_user?.email.toLowerCase()}`}
                              sx={{
                                textDecoration: 'none',
                                color: 'inherit',
                              }}
                              component={RouterLink}>
                              {displayUserName(users[0].active_user)}
                            </Link>
                          </TableCell>
                          <TableCell>
                            <Link
                              to={`/users/${users[0].active_user?.email.toLowerCase()}`}
                              sx={{
                                textDecoration: 'none',
                                color: 'inherit',
                              }}
                              component={RouterLink}>
                              {users[0].active_user?.email.toLowerCase()}
                            </Link>
                          </TableCell>
                          <TableCell>
                            <Ending memberships={users} />
                          </TableCell>
                        </TableRow>
                      ))
                  ) : (
                    <EmptyListEntry />
                  )}
                </TableBody>
                
                <TableFooter>
                  <TableRow />
                </TableFooter>
              </Table>

            </TableContainer>
            </AccordionDetails>
            </Accordion>)
}
*/
