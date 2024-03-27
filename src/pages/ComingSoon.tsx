import React from 'react';
import Grid from '@mui/material/Grid';
import Paper from '@mui/material/Paper';
import Box from '@mui/material/Box';
import Typography from '@mui/material/Typography';

export default function ComingSoon() {
  return (
    <React.Fragment>
      <Paper>
        <Grid container spacing={2} sx={{padding: 2}}>
          <Grid item xs={12}>
            <Grid container justifyContent="center">
              <Grid item>
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
          <Grid item xs={12}>
            <Grid container justifyContent="center">
              <Grid item>
                <Typography component="h2" variant="h3" color="primary">
                  Coming Soon!
                </Typography>
              </Grid>
            </Grid>
          </Grid>
        </Grid>
      </Paper>
    </React.Fragment>
  );
}
