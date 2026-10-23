import React from 'react';
import Grid from '@mui/material/Grid';
import Box from '@mui/material/Box';
import Typography from '@mui/material/Typography';

export default function NotFound() {
  return (
    <React.Fragment>
      <Grid container spacing={2} sx={{padding: 2}}>
        <Grid size={12}>
          <Grid
            container
            sx={{
              justifyContent: 'center',
            }}>
            <Grid>
              <Box
                component="img"
                src="/logo.png"
                alt="Access logo"
                sx={{
                  width: 350,
                }}
              />
            </Grid>
          </Grid>
        </Grid>
        <Grid size={12}>
          <Grid
            container
            sx={{
              justifyContent: 'center',
            }}>
            <Grid>
              <Typography
                component="h2"
                variant="h3"
                sx={{
                  color: 'text.accent',
                }}>
                An Unrecoverable Error Occurred
              </Typography>
            </Grid>
          </Grid>
        </Grid>
      </Grid>
    </React.Fragment>
  );
}
